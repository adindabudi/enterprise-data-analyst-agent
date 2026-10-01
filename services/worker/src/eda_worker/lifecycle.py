"""The task lifecycle, driven as a plain loop by whoever owns the task.

This is the task's whole run (intake, then
analysis passes, then a terminal result), without a workflow engine: the
application's supervisor calls it directly, and ownership is re-checked before
every pass and before anything is published.

The rules are the ones users already rely on:

* Cancellation is honoured at every boundary, and a cancelled run is recorded as
  cancelled, never as a failure or a success.
* A steering instruction that arrives during a pass starts another pass, up to a
  small budget, instead of being silently dropped.
* A pass that did not produce the requested files is repaired from what it
  already built, up to a small budget, and never by dropping a requirement.
* Anything else that goes wrong is a failure with a bounded, safe code.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from eda_contracts.tasks import TaskStatus
from eda_runtime_state.models import TERMINAL, TaskRecord

from .contracts import ControlSnapshot

MAX_STEERING_ROUNDS = 3
MAX_OUTPUT_REPAIR_ROUNDS = 2
# Intake and the terminal step were supersteps in the workflow; what remains bounds analysis passes.
MAX_ANALYSIS_PASSES = 1 + MAX_STEERING_ROUNDS + MAX_OUTPUT_REPAIR_ROUNDS
logger = logging.getLogger(__name__)
_SAFE_DECLARED_FAILURE = re.compile(r"^analysis_failed_[a-z0-9_]{1,112}$")
_SAFE_PROVIDER_CODE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class OutputRepairRequired(ValueError):
    pass


class OwnershipLost(RuntimeError):
    """Another executor now owns the task; this one must stop without writing anything."""


class AnalysisServices(Protocol):
    async def task(self, task_id: str) -> TaskRecord | None: ...

    async def controls(self, task_id: str) -> ControlSnapshot: ...

    async def checkpoint(self, task_id: str, status: TaskStatus, expected_checkpoint: int) -> TaskRecord: ...

    async def run_analysis(
        self,
        task_id: str,
        pending_command_ids: tuple[str, ...],
        repair_feedback: str | None = None,
    ) -> str: ...

    async def acknowledge(self, task_id: str, through_sequence: int) -> None: ...

    async def complete(self, task_id: str, text: str) -> str: ...

    async def cancel(self, task_id: str) -> None: ...

    async def fail(self, task_id: str, failure_code: str) -> None: ...


# Called before each pass and before publication; raises OwnershipLost when the lease is gone.
OwnershipCheck = Callable[[], Awaitable[None]]
# Called when a pass starts, so a live view can drop text from the pass it replaces.
PassObserver = Callable[[int], Awaitable[None]]


@dataclass(frozen=True)
class LifecycleResult:
    task_id: str
    status: TaskStatus
    final_message_id: str | None = None
    failure_code: str | None = None


async def _no_check() -> None:
    return None


async def _no_observer(pass_index: int) -> None:
    del pass_index


async def run_task_lifecycle(
    services: AnalysisServices,
    task_id: str,
    *,
    ensure_owner: OwnershipCheck = _no_check,
    on_pass: PassObserver = _no_observer,
) -> LifecycleResult:
    task = await services.task(task_id)
    if task is None:
        return LifecycleResult(task_id, TaskStatus.FAILED, failure_code="task_unavailable")
    if task.status in TERMINAL:
        return LifecycleResult(task.id, task.status, final_message_id=task.final_message_id)
    await ensure_owner()
    controls = await services.controls(task.id)
    if controls.cancellation_requested:
        return await _cancel(services, task)
    if task.status is TaskStatus.PLANNING:
        task = await services.checkpoint(task.id, TaskStatus.ANALYZING, task.checkpoint_sequence)
    if task.status is not TaskStatus.ANALYZING:
        return await _fail(services, task, "unsupported_task_phase")

    steering_round = 0
    repair_round = 0
    repair_feedback: str | None = None
    for pass_index in range(MAX_ANALYSIS_PASSES):
        current = await services.task(task_id)
        if current is None:
            return LifecycleResult(task_id, TaskStatus.FAILED, failure_code="task_unavailable")
        if current.status in TERMINAL:
            return LifecycleResult(current.id, current.status, final_message_id=current.final_message_id)
        await ensure_owner()
        controls = await services.controls(current.id)
        if controls.cancellation_requested:
            return await _cancel(services, current)
        await on_pass(pass_index)
        try:
            text = (
                await services.run_analysis(
                    current.id,
                    controls.pending_command_ids,
                    repair_feedback=repair_feedback,
                )
            ).strip()
            if not text:
                return await _fail(services, current, "empty_agent_response")
            if controls.highest_command_sequence > 0:
                await services.acknowledge(current.id, controls.highest_command_sequence)
            latest_controls = await services.controls(current.id)
            if latest_controls.cancellation_requested:
                return await _cancel(services, current)
            if latest_controls.pending_command_ids:
                if steering_round >= MAX_STEERING_ROUNDS:
                    return await _fail(services, current, "steering_budget_exhausted")
                steering_round += 1
                continue
            await ensure_owner()
            final_message_id = await services.complete(current.id, text)
            completed = await services.checkpoint(current.id, TaskStatus.COMPLETED, current.checkpoint_sequence)
            return LifecycleResult(completed.id, completed.status, final_message_id=final_message_id)
        except OutputRepairRequired as error:
            if repair_round >= MAX_OUTPUT_REPAIR_ROUNDS:
                return await _fail(services, current, "output_repair_budget_exhausted")
            repair_round += 1
            repair_feedback = str(error)[:2000]
        except OwnershipLost:
            raise
        except asyncio.CancelledError:
            latest = await asyncio.shield(services.task(current.id))
            if latest is not None and latest.cancellation_requested:
                if latest.status in TERMINAL:
                    # Settled elsewhere already; only the sandbox work is left to stop.
                    await asyncio.shield(services.cancel(latest.id))
                else:
                    await asyncio.shield(_cancel(services, latest))
            raise
        except Exception as error:
            logger.exception("analysis execution failed for task %s", current.id)
            return await _fail(services, current, analysis_failure_code(error))
    latest = await services.task(task_id)
    if latest is None:
        return LifecycleResult(task_id, TaskStatus.FAILED, failure_code="task_unavailable")
    return await _fail(services, latest, "analysis_pass_budget_exhausted")


def analysis_failure_code(error: Exception) -> str:
    declared = getattr(error, "failure_code", None)
    if isinstance(declared, str) and _SAFE_DECLARED_FAILURE.fullmatch(declared):
        return declared
    tokens = ["analysis_failed", failure_token(type(error).__name__)]
    status = getattr(error, "status_code", None)
    if isinstance(status, int) and 100 <= status <= 599:
        tokens.append(f"http_{status}")
    provider_code = getattr(error, "code", None)
    if isinstance(provider_code, str) and _SAFE_PROVIDER_CODE.fullmatch(provider_code):
        tokens.append(failure_token(provider_code))
    return "_".join(tokens)[:128]


def failure_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


async def _cancel(services: AnalysisServices, task: TaskRecord) -> LifecycleResult:
    current = task
    if current.status is not TaskStatus.CANCELLING:
        current = await services.checkpoint(current.id, TaskStatus.CANCELLING, current.checkpoint_sequence)
    await services.cancel(current.id)
    cancelled = await services.checkpoint(current.id, TaskStatus.CANCELLED, current.checkpoint_sequence)
    return LifecycleResult(cancelled.id, cancelled.status)


async def _fail(services: AnalysisServices, task: TaskRecord, failure_code: str) -> LifecycleResult:
    await services.fail(task.id, failure_code)
    failed = task
    if task.status not in TERMINAL:
        failed = await services.checkpoint(task.id, TaskStatus.FAILED, task.checkpoint_sequence)
    return LifecycleResult(failed.id, failed.status, failure_code=failure_code)
