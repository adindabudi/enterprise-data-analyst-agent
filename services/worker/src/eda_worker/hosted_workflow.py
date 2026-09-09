from __future__ import annotations

import asyncio
import json
import logging
import re
from enum import StrEnum
from typing import Any, Never, Protocol, cast

from agent_framework import Executor, Message, Workflow, WorkflowBuilder, WorkflowContext, handler
from eda_contracts.tasks import TaskStatus
from eda_runtime_state.models import TERMINAL, TaskRecord
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from .contracts import ControlSnapshot

MAX_STEERING_ROUNDS = 3
MAX_OUTPUT_REPAIR_ROUNDS = 2
MAX_WORKFLOW_ITERATIONS = 12
logger = logging.getLogger(__name__)
_SAFE_DECLARED_FAILURE = re.compile(r"^analysis_failed_[a-z0-9_]{1,112}$")
_SAFE_PROVIDER_CODE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class OutputRepairRequired(ValueError):
    pass


class HostedWorkflowModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        serialize_by_alias=True,
    )


class WorkflowAction(StrEnum):
    ANALYZE = "analyze"
    TERMINAL = "terminal"


class WorkflowCommand(HostedWorkflowModel):
    task_id: str = Field(pattern=r"^task_[A-Za-z0-9_-]{8,}$")
    action: WorkflowAction
    steering_round: int = Field(default=0, ge=0)
    repair_round: int = Field(default=0, ge=0, le=MAX_OUTPUT_REPAIR_ROUNDS)
    repair_feedback: str | None = Field(default=None, max_length=2000)
    status: TaskStatus | None = None
    final_message_id: str | None = None
    failure_code: str | None = None


class HostedTaskResult(HostedWorkflowModel):
    task_id: str
    status: TaskStatus
    final_message_id: str | None = None
    failure_code: str | None = None


class AnalysisServices(Protocol):
    async def task(self, task_id: str) -> TaskRecord | None: ...

    async def controls(self, task_id: str) -> ControlSnapshot: ...

    async def checkpoint(self, task_id: str, status: TaskStatus, expected_checkpoint: int) -> TaskRecord: ...

    async def run_analysis(
        self, task_id: str, pending_command_ids: tuple[str, ...], repair_feedback: str | None = None,
    ) -> str: ...

    async def acknowledge(self, task_id: str, through_sequence: int) -> None: ...

    async def complete(self, task_id: str, text: str) -> str: ...

    async def cancel(self, task_id: str) -> None: ...

    async def fail(self, task_id: str, failure_code: str) -> None: ...


class IntakeExecutor(Executor):
    def __init__(self, services: AnalysisServices, id: str = "intake") -> None:
        super().__init__(id=id)
        self._services = services

    @handler
    async def start(
        self,
        messages: list[Message],
        ctx: WorkflowContext[WorkflowCommand, Never],
    ) -> None:
        task_id = _task_id_from_messages(messages)
        task = await self._services.task(task_id)
        if task is None:
            await ctx.send_message(_terminal(task_id, TaskStatus.FAILED, failure_code="task_unavailable"))
            return
        if task.status in TERMINAL:
            await ctx.send_message(
                _terminal(
                    task.id,
                    task.status,
                    final_message_id=task.final_message_id,
                )
            )
            return
        controls = await self._services.controls(task.id)
        if controls.cancellation_requested:
            await ctx.send_message(await _cancel(self._services, task))
            return
        if task.status is TaskStatus.PLANNING:
            task = await self._services.checkpoint(
                task.id,
                TaskStatus.ANALYZING,
                task.checkpoint_sequence,
            )
        if task.status is not TaskStatus.ANALYZING:
            await ctx.send_message(await _fail(self._services, task, "unsupported_task_phase"))
            return
        await ctx.send_message(
            WorkflowCommand(
                task_id=task.id,
                action=WorkflowAction.ANALYZE,
            )
        )


class AnalyzeExecutor(Executor):
    def __init__(self, services: AnalysisServices, id: str = "analyze") -> None:
        super().__init__(id=id)
        self._services = services

    @handler
    async def analyze(
        self,
        command: WorkflowCommand,
        ctx: WorkflowContext[WorkflowCommand, Never],
    ) -> None:
        task = await self._services.task(command.task_id)
        if task is None:
            await ctx.send_message(_terminal(command.task_id, TaskStatus.FAILED, failure_code="task_unavailable"))
            return
        controls = await self._services.controls(task.id)
        if controls.cancellation_requested:
            await ctx.send_message(await _cancel(self._services, task))
            return
        try:
            text = (await self._services.run_analysis(
                task.id, controls.pending_command_ids, repair_feedback=command.repair_feedback,
            )).strip()
            if not text:
                await ctx.send_message(await _fail(self._services, task, "empty_agent_response"))
                return
            if controls.highest_command_sequence > 0:
                await self._services.acknowledge(task.id, controls.highest_command_sequence)
            latest_controls = await self._services.controls(task.id)
            if latest_controls.cancellation_requested:
                await ctx.send_message(await _cancel(self._services, task))
                return
            if latest_controls.pending_command_ids:
                if command.steering_round >= MAX_STEERING_ROUNDS:
                    await ctx.send_message(await _fail(self._services, task, "steering_budget_exhausted"))
                    return
                await ctx.send_message(command.model_copy(update={"steering_round": command.steering_round + 1}))
                return
            final_message_id = await self._services.complete(task.id, text)
            completed = await self._services.checkpoint(
                task.id,
                TaskStatus.COMPLETED,
                task.checkpoint_sequence,
            )
            await ctx.send_message(
                _terminal(
                    completed.id,
                    completed.status,
                    final_message_id=final_message_id,
                )
            )
        except OutputRepairRequired as error:
            if command.repair_round >= MAX_OUTPUT_REPAIR_ROUNDS:
                await ctx.send_message(await _fail(self._services, task, "output_repair_budget_exhausted"))
                return
            await ctx.send_message(command.model_copy(update={
                "repair_round": command.repair_round + 1,
                "repair_feedback": str(error)[:2000],
            }))
        except asyncio.CancelledError:
            latest = await self._services.task(task.id)
            if latest is not None and latest.cancellation_requested:
                await _cancel(self._services, latest)
            raise
        except Exception as error:
            logger.exception("analysis execution failed for task %s", task.id)
            await ctx.send_message(await _fail(self._services, task, _analysis_failure_code(error)))


class TerminalExecutor(Executor):
    def __init__(self, id: str = "terminal") -> None:
        super().__init__(id=id)

    @handler
    async def finish(
        self,
        command: WorkflowCommand,
        ctx: WorkflowContext[Never, str],
    ) -> None:
        if command.status is None:
            raise ValueError("terminal workflow command is missing status")
        result = HostedTaskResult(
            task_id=command.task_id,
            status=command.status,
            final_message_id=command.final_message_id,
            failure_code=command.failure_code,
        )
        await ctx.yield_output(result.model_dump_json(by_alias=True))


def build_hosted_workflow(services: AnalysisServices) -> Workflow:
    intake = IntakeExecutor(services)
    analyze = AnalyzeExecutor(services)
    terminal = TerminalExecutor()
    return (
        WorkflowBuilder(
            name="enterprise-data-analyst-long-job",
            max_iterations=MAX_WORKFLOW_ITERATIONS,
            start_executor=intake,
            output_from=[terminal],
        )
        .add_edge(intake, analyze, condition=_is_analysis)
        .add_edge(intake, terminal, condition=_is_terminal)
        .add_edge(analyze, analyze, condition=_is_analysis)
        .add_edge(analyze, terminal, condition=_is_terminal)
        .build()
    )


def _is_analysis(command: WorkflowCommand) -> bool:
    return command.action is WorkflowAction.ANALYZE


def _is_terminal(command: WorkflowCommand) -> bool:
    return command.action is WorkflowAction.TERMINAL


def _task_id_from_messages(messages: list[Message]) -> str:
    for message in reversed(messages):
        if message.role != "user" or not message.text:
            continue
        try:
            payload: Any = json.loads(message.text)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            task_id = cast(dict[str, object], payload).get("taskId")
            if isinstance(task_id, str):
                return WorkflowCommand(task_id=task_id, action=WorkflowAction.ANALYZE).task_id
    raise ValueError("hosted workflow input is missing a valid taskId")


def _terminal(
    task_id: str,
    status: TaskStatus,
    *,
    final_message_id: str | None = None,
    failure_code: str | None = None,
) -> WorkflowCommand:
    return WorkflowCommand(
        task_id=task_id,
        action=WorkflowAction.TERMINAL,
        status=status,
        final_message_id=final_message_id,
        failure_code=failure_code,
    )


def _analysis_failure_code(error: Exception) -> str:
    declared = getattr(error, "failure_code", None)
    if isinstance(declared, str) and _SAFE_DECLARED_FAILURE.fullmatch(declared):
        return declared
    tokens = ["analysis_failed", _failure_token(type(error).__name__)]
    status = getattr(error, "status_code", None)
    if isinstance(status, int) and 100 <= status <= 599:
        tokens.append(f"http_{status}")
    provider_code = getattr(error, "code", None)
    if isinstance(provider_code, str) and _SAFE_PROVIDER_CODE.fullmatch(provider_code):
        tokens.append(_failure_token(provider_code))
    return "_".join(tokens)[:128]


def _failure_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


async def _cancel(services: AnalysisServices, task: TaskRecord) -> WorkflowCommand:
    current = task
    if current.status is not TaskStatus.CANCELLING:
        current = await services.checkpoint(
            current.id,
            TaskStatus.CANCELLING,
            current.checkpoint_sequence,
        )
    await services.cancel(current.id)
    cancelled = await services.checkpoint(
        current.id,
        TaskStatus.CANCELLED,
        current.checkpoint_sequence,
    )
    return _terminal(cancelled.id, cancelled.status)


async def _fail(services: AnalysisServices, task: TaskRecord, failure_code: str) -> WorkflowCommand:
    await services.fail(task.id, failure_code)
    failed = task
    if task.status not in TERMINAL:
        failed = await services.checkpoint(
            task.id,
            TaskStatus.FAILED,
            task.checkpoint_sequence,
        )
    return _terminal(failed.id, failed.status, failure_code=failure_code)
