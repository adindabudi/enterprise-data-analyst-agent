"""The analysis runtime as services a task lifecycle can drive, with no hosting framework.

These services run inside the API application.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from typing import Any, Protocol, cast

from agent_framework import AgentResponse, Content
from eda_contracts.tasks import TaskStatus
from eda_runtime_state.models import TaskRecord

from .contracts import ControlSnapshot, PublicationResult
from .lifecycle import AnalysisServices, OutputRepairRequired
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

# Receives visible text as the model produces it; the owner decides how to batch and publish it.
DeltaSink = Callable[[str], Awaitable[None]]
DeltaSinkFactory = Callable[[str], DeltaSink | None]


class AnalysisAgentResponseError(ValueError):
    def __init__(self, diagnostic: str, *, failure_code: str) -> None:
        super().__init__(f"analysis agent returned error content ({diagnostic})")
        self.failure_code = failure_code


class ClosableResources(Protocol):
    async def aclose(self) -> None: ...


class AnalysisRuntimeHandle(Protocol):
    repository: Any
    primary_agent: Any
    activities: Any
    resources: ClosableResources
    output_planner: OutputContractPlanner


RuntimeFactory = Callable[[Any], Awaitable[AnalysisRuntimeHandle]]


class AnalysisRuntimeProvider:
    """Builds the runtime once, on first use, and closes it exactly once."""

    def __init__(self, settings: Any, *, runtime_factory: RuntimeFactory | None = None) -> None:
        self._settings = settings
        self._runtime_factory = runtime_factory
        self._runtime: AnalysisRuntimeHandle | None = None
        self._lock = asyncio.Lock()
        self._closed = False

    async def get(self) -> AnalysisRuntimeHandle:
        if self._closed:
            raise RuntimeError("analysis runtime is closed")
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


class RuntimeAnalysisServices(AnalysisServices):
    def __init__(
        self,
        provider: AnalysisRuntimeProvider,
        *,
        delta_sink_factory: DeltaSinkFactory | None = None,
        cancel_reason: str = "task_lifecycle",
    ) -> None:
        self._provider = provider
        self._delta_sink_factory = delta_sink_factory
        self._cancel_reason = cancel_reason

    async def task(self, task_id: str) -> TaskRecord | None:
        runtime = await self._provider.get()
        return cast(TaskRecord | None, await runtime.repository.resolve_task(task_id))

    async def warmup(self) -> None:
        """Build the runtime before the first task needs it, so a broken configuration shows at startup."""
        await self._provider.get()

    async def reset_execution(self, task_id: str) -> None:
        """Delete sandbox work a previous owner may have left running.

        There is no command-level cancel, so deletion is the only guaranteed stop.
        Committed outputs are unaffected; an uncommitted execution reruns in a fresh
        sandbox, which has no egress and no credentials, so the rerun reaches nothing
        outside it.
        """
        runtime = await self._provider.get()
        await runtime.activities.cancel_task({"taskId": task_id, "reason": "recovery_reset"})

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
        options = {
            "task_id": task_id,
            "phase": "chat",
            "work_class": "analysis",
            "pending_command_ids": list(pending_command_ids),
        }
        sink = self._delta_sink_factory(task_id) if self._delta_sink_factory is not None else None
        if sink is None:
            response = await runtime.primary_agent.run(instruction, stream=False, options=options)
            return response_text(response)
        stream = runtime.primary_agent.run(instruction, stream=True, options=options)
        boundary = getattr(sink, "boundary", None)
        async for update in stream:
            if callable(boundary) and _moves_past_text(update):
                await cast(Callable[[], Awaitable[None]], boundary)()
            text = getattr(update, "text", None)
            if isinstance(text, str) and text:
                await sink(text)
        # Buffered text must reach the live view before publication announces the final answer.
        flush = getattr(sink, "flush", None)
        if callable(flush):
            await cast(Callable[[], Awaitable[None]], flush)()
        return response_text(await stream.get_final_response())

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
        await runtime.activities.cancel_task({"taskId": task_id, "reason": self._cancel_reason})

    async def fail(self, task_id: str, failure_code: str) -> None:
        runtime = await self._provider.get()
        await runtime.activities.fail_task(
            {
                "taskId": task_id,
                "failureCode": failure_code,
                "diagnosticRef": f"diag:{task_id}",
            }
        )


def _moves_past_text(update: object) -> bool:
    """A tool call or result in the stream means the text before it was not the answer."""
    contents = getattr(update, "contents", None)
    if not isinstance(contents, list):
        return False
    return any(
        getattr(content, "type", None) in {"function_call", "function_result"}
        for content in cast(list[object], contents)
    )


def response_text(value: object) -> str:
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
