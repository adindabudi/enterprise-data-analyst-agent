from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from typing import Any, Protocol, cast

from agent_framework import AgentResponse, Content
from agent_framework_foundry_hosting import ResponsesHostServer
from azure.ai.agentserver.responses import ResponsesServerOptions
from eda_contracts.tasks import TaskStatus
from eda_runtime_state.models import TaskRecord

from .contracts import ControlSnapshot, PublicationResult
from .hosted_workflow import AnalysisServices, OutputRepairRequired, build_hosted_workflow
from .output_planning import OutputContractPlanner

RUN_INSTRUCTION = (
    "Finish the current user request in this bounded analysis pass. Use tools when the request needs them, "
    "and when it asks for a file, build, validate, and publish it rather than describing what you would build."
    " Follow the task's required output contract. Do not add unrequested formats or extra deliverables."
    " Reuse completed source queries and validated artifacts. Finish when the requested outputs and plan are complete."
    " A final explanation or completed to-do list cannot replace a requested file."
)
_SAFE_ERROR_CODE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_HTTP_STATUS = re.compile(r"\b(?:400|401|403|404|408|409|422|424|429|500|502|503|504)\b")
_EXCEPTION_TYPE = re.compile(r"\b[A-Z][A-Za-z0-9]*(?:Error|Exception)\b")


class AnalysisAgentResponseError(ValueError):
    def __init__(self, diagnostic: str, *, failure_code: str) -> None:
        super().__init__(f"analysis agent returned error content ({diagnostic})")
        self.failure_code = failure_code


class ClosableResources(Protocol):
    async def aclose(self) -> None: ...


class AnalysisRuntime(Protocol):
    repository: Any
    primary_agent: Any
    activities: Any
    resources: ClosableResources
    output_planner: OutputContractPlanner


RuntimeFactory = Callable[[Any], Awaitable[AnalysisRuntime]]


class HostedRuntimeProvider:
    def __init__(self, settings: Any, *, runtime_factory: RuntimeFactory | None = None) -> None:
        self._settings = settings
        self._runtime_factory = runtime_factory
        self._runtime: AnalysisRuntime | None = None
        self._lock = asyncio.Lock()
        self._closed = False

    async def get(self) -> AnalysisRuntime:
        if self._closed:
            raise RuntimeError("hosted runtime is closed")
        if self._runtime is not None:
            return self._runtime
        async with self._lock:
            if self._runtime is None:
                factory = self._runtime_factory
                if factory is None:
                    from .runtime import build_analysis_runtime

                    factory = cast(RuntimeFactory, build_analysis_runtime)
                self._runtime = await factory(self._settings)
        return self._runtime

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._runtime is not None:
            await self._runtime.resources.aclose()


class HostedAnalysisServices(AnalysisServices):
    def __init__(self, provider: HostedRuntimeProvider) -> None:
        self._provider = provider

    async def task(self, task_id: str) -> TaskRecord | None:
        runtime = await self._provider.get()
        return cast(TaskRecord | None, await runtime.repository.resolve_task(task_id))

    async def controls(self, task_id: str) -> ControlSnapshot:
        runtime = await self._provider.get()
        payload = await runtime.activities.load_task_controls({"taskId": task_id, "includeCommands": True})
        return ControlSnapshot.model_validate(payload)

    async def checkpoint(self, task_id: str, status: TaskStatus, expected_checkpoint: int) -> TaskRecord:
        runtime = await self._provider.get()
        await runtime.activities.checkpoint_task(
            {
                "taskId": task_id,
                "status": status.value,
                "expectedCheckpoint": expected_checkpoint,
            }
        )
        task = await runtime.repository.resolve_task(task_id)
        if task is None:
            raise ValueError("task is unavailable after checkpoint")
        return cast(TaskRecord, task)

    async def run_analysis(
        self,
        task_id: str,
        pending_command_ids: tuple[str, ...],
        repair_feedback: str | None = None,
    ) -> str:
        runtime = await self._provider.get()
        await runtime.output_planner.ensure(task_id, pending_command_ids=pending_command_ids)
        instruction = RUN_INSTRUCTION
        if repair_feedback is not None:
            instruction += (
                " The deterministic completion check rejected the previous pass: "
                + repair_feedback
                + ". Repair only missing or invalid deliverables using existing input and result artifacts. "
                "Do not drop requirements, repeat completed source queries, or claim that blocked work is complete."
            )
        response = await runtime.primary_agent.run(
            instruction,
            stream=False,
            options={
                "task_id": task_id,
                "phase": "chat",
                "work_class": "analysis",
                "pending_command_ids": list(pending_command_ids),
            },
        )
        return _response_text(response)

    async def acknowledge(self, task_id: str, through_sequence: int) -> None:
        runtime = await self._provider.get()
        await runtime.activities.acknowledge_task_commands({"taskId": task_id, "throughSequence": through_sequence})

    async def complete(self, task_id: str, text: str) -> str:
        runtime = await self._provider.get()
        validation = await runtime.activities.validate_outputs({"taskId": task_id, "requireOutputContract": True})
        if validation.get("outcome") != "passed":
            reason = validation.get("reportRef", "unknown-output-failure")
            if reason.startswith("missing-required-outputs:") or reason in {
                "no-published-artifacts",
                "unfinished-plan",
            }:
                raise OutputRepairRequired(reason)
            raise AnalysisAgentResponseError(
                "missing validated requested outputs",
                failure_code="analysis_failed_missing_validated_outputs",
            )
        publication = PublicationResult.model_validate(
            await runtime.activities.complete_chat({"taskId": task_id, "text": text})
        )
        return publication.final_message_id

    async def cancel(self, task_id: str) -> None:
        runtime = await self._provider.get()
        await runtime.activities.cancel_task({"taskId": task_id, "reason": "hosted_workflow"})

    async def fail(self, task_id: str, failure_code: str) -> None:
        runtime = await self._provider.get()
        await runtime.activities.fail_task(
            {
                "taskId": task_id,
                "failureCode": failure_code,
                "diagnosticRef": f"diag:{task_id}",
            }
        )


def create_hosted_server(
    settings: Any,
    *,
    runtime_factory: RuntimeFactory | None = None,
) -> ResponsesHostServer:
    provider = HostedRuntimeProvider(settings, runtime_factory=runtime_factory)
    services = HostedAnalysisServices(provider)
    workflow_agent = build_hosted_workflow(services).as_agent(
        id="enterprise-data-analyst-long-job",
        name="enterprise-data-analyst-long-job",
        description="Deterministic long-running enterprise data analysis workflow",
    )
    server = ResponsesHostServer(
        workflow_agent,
        options=ResponsesServerOptions(resilient_background=True),
        log_level="INFO",
    )
    server.shutdown_handler(provider.close)
    return server


def run_hosted_server(settings: Any) -> None:
    create_hosted_server(settings).run()


def _response_text(value: object) -> str:
    if not isinstance(value, AgentResponse):
        raise TypeError("analysis agent must return AgentResponse")
    replies = [message for message in value.messages if getattr(message, "role", None) == "assistant"]
    text = (replies[-1].text if replies else value.text).strip()
    if not text:
        errors = [content for message in value.messages for content in message.contents if content.type == "error"]
        if errors:
            raise AnalysisAgentResponseError(
                _error_diagnostic(errors),
                failure_code=_error_failure_code(errors),
            )
        raise ValueError("analysis agent returned no visible response")
    return text


def _error_diagnostic(errors: list[Content]) -> str:
    codes = sorted(
        {
            content.error_code
            for content in errors
            if content.error_code is not None and _SAFE_ERROR_CODE.fullmatch(content.error_code)
        }
    )
    raw_text = " ".join(
        value for content in errors for value in (content.message, content.error_details) if isinstance(value, str)
    )
    statuses = sorted(set(_HTTP_STATUS.findall(raw_text)))
    exception_types = sorted(set(_EXCEPTION_TYPE.findall(raw_text)))
    return "; ".join(
        (
            f"codes={','.join(codes) or 'unknown'}",
            f"http_statuses={','.join(statuses) or 'unknown'}",
            f"exception_types={','.join(exception_types) or 'unknown'}",
        )
    )


def _error_failure_code(errors: list[Content]) -> str:
    codes = sorted(
        {
            _failure_token(content.error_code)
            for content in errors
            if content.error_code is not None and _SAFE_ERROR_CODE.fullmatch(content.error_code)
        }
    )
    raw_text = " ".join(
        value for content in errors for value in (content.message, content.error_details) if isinstance(value, str)
    )
    statuses = [f"http_{status}" for status in sorted(set(_HTTP_STATUS.findall(raw_text)))]
    exception_types = [_failure_token(name) for name in sorted(set(_EXCEPTION_TYPE.findall(raw_text)))]
    tokens = ["analysis_failed", "agent_response", *codes, *statuses, *exception_types]
    return "_".join(token for token in tokens if token)[:128]


def _failure_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
