from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from eda_contracts.controls import CommandKind
from eda_contracts.tasks import TaskStatus
from eda_runtime_state.events import EventDraft
from eda_runtime_state.tasks import RuntimeStateRepository
from redis.exceptions import RedisError

from eda_worker.metrics import record_cancel_ack, record_task_completed, record_task_failed

logger = logging.getLogger(__name__)


class EventWriter(Protocol):
    async def append(self, draft: EventDraft) -> object: ...


@dataclass(frozen=True)
class ActivitySet:
    checkpoint_task: Any
    load_task_controls: Any
    acknowledge_task_commands: Any
    complete_chat: Any
    validate_outputs: Any
    publish_outputs: Any
    cancel_task: Any
    fail_task: Any
    additional: tuple[Any, ...] = ()

    def all(self) -> tuple[Any, ...]:
        return (
            self.checkpoint_task,
            self.load_task_controls,
            self.acknowledge_task_commands,
            self.complete_chat,
            self.validate_outputs,
            self.publish_outputs,
            self.cancel_task,
            self.fail_task,
            *self.additional,
        )


ActivityHandler = Callable[[dict[str, object]], Awaitable[dict[str, str]]]


def create_activities(
    repository: RuntimeStateRepository,
    event_store: EventWriter,
    *,
    validate_outputs_handler: ActivityHandler | None = None,
    complete_chat_handler: ActivityHandler | None = None,
    publish_outputs_handler: ActivityHandler | None = None,
    cancel_task_handler: ActivityHandler | None = None,
    fail_task_handler: ActivityHandler | None = None,
    additional: tuple[Any, ...] = (),
) -> ActivitySet:
    async def checkpoint_task(payload: dict[str, object]):
        task_id = payload.get("taskId")
        raw_status = payload.get("status")
        expected_checkpoint = payload.get("expectedCheckpoint")
        if not isinstance(task_id, str) or not isinstance(raw_status, str) or not isinstance(expected_checkpoint, int):
            raise ValueError("checkpoint activity payload is invalid")
        status = TaskStatus(raw_status)
        task = await repository.transition_task(task_id, status, expected_checkpoint)
        if task.status is TaskStatus.COMPLETED:
            record_task_completed()
        elif task.status in {TaskStatus.FAILED, TaskStatus.FAILED_CANCELLATION}:
            record_task_failed()
        terminal_types = {
            TaskStatus.COMPLETED: "run.completed",
            TaskStatus.FAILED: "run.failed",
            TaskStatus.CANCELLED: "run.cancelled",
            TaskStatus.FAILED_CANCELLATION: "run.failed",
        }
        try:
            await event_store.append(
                EventDraft(
                    session_id=task.session_id,
                    task_id=task.id,
                    type="task.checkpointed",
                    payload={"status": task.status.value, "checkpointSequence": task.checkpoint_sequence},
                )
            )
            terminal_type = terminal_types.get(task.status)
            if terminal_type is not None:
                await event_store.append(
                    EventDraft(
                        session_id=task.session_id,
                        task_id=task.id,
                        type=terminal_type,
                        payload={
                            "status": task.status.value,
                            "finalMessageId": task.final_message_id,
                        },
                    )
                )
            if task.status is TaskStatus.ANALYZING:
                await event_store.append(
                    EventDraft(
                        session_id=task.session_id,
                        task_id=task.id,
                        type="analysis_progress",
                        payload={
                            "milestone": "Agent is thinking",
                            "detail": "Preparing tools and response.",
                            "state": "running",
                        },
                    )
                )
        except (ConnectionError, RedisError):
            pass
        return {
            "status": task.status.value,
            "checkpointSequence": task.checkpoint_sequence,
        }

    async def load_task_controls(payload: dict[str, object]) -> dict[str, object]:
        task_id = payload.get("taskId")
        include_commands = payload.get("includeCommands", True)
        if not isinstance(task_id, str):
            raise ValueError("control activity payload is invalid")
        if not isinstance(include_commands, bool):
            raise ValueError("control activity command mode is invalid")
        task = await repository.resolve_task(task_id)
        if task is None:
            return {
                "cancellationRequested": False,
                "pendingCommandIds": [],
                "highestCommandSequence": 0,
                "authResumed": False,
            }
        commands = await repository.pending_commands(task_id) if include_commands else []
        return {
            "cancellationRequested": task.cancellation_requested,
            "pendingCommandIds": [command.id for command in commands],
            "highestCommandSequence": max((command.sequence for command in commands), default=0),
            "authResumed": any(command.kind is CommandKind.AUTH_RESUMED for command in commands),
        }

    async def acknowledge_task_commands(payload: dict[str, object]):
        task_id = payload.get("taskId")
        through_sequence = payload.get("throughSequence")
        if not isinstance(task_id, str) or not isinstance(through_sequence, int):
            raise ValueError("command acknowledgement payload is invalid")
        return await repository.acknowledge_commands(task_id, through_sequence)

    async def validate_outputs(payload: dict[str, object]) -> dict[str, str]:
        if validate_outputs_handler is not None:
            return await validate_outputs_handler(payload)
        return {"outcome": "failed", "reportRef": "unconfigured"}

    async def complete_chat(payload: dict[str, object]) -> dict[str, str]:
        if complete_chat_handler is None:
            raise RuntimeError("chat completion handler is unavailable")
        return await complete_chat_handler(payload)

    async def publish_outputs(payload: dict[str, object]) -> dict[str, str]:
        if publish_outputs_handler is not None:
            return await publish_outputs_handler(payload)
        return {"finalMessageId": "unconfigured"}

    async def cancel_task(payload: dict[str, object]) -> dict[str, str]:
        await record_cancellation_latency(payload.get("taskId"))
        if cancel_task_handler is not None:
            return await cancel_task_handler(payload)
        return {"outcome": "cancelled"}

    async def record_cancellation_latency(task_id: object) -> None:
        if not isinstance(task_id, str):
            return
        try:
            commands = await repository.pending_commands(task_id)
            requested = [command.created_at for command in commands if command.kind is CommandKind.CANCEL]
            if requested:
                record_cancel_ack((datetime.now(UTC) - min(requested)).total_seconds() * 1000)
        except Exception:
            # Measuring the objective must never be the reason a cancellation fails to land.
            logger.exception("cancellation latency could not be measured for task %s", task_id)

    async def fail_task(payload: dict[str, object]) -> dict[str, str]:
        logger.error(
            "task failed: code=%s ref=%s task=%s",
            payload.get("failureCode"),
            payload.get("diagnosticRef"),
            payload.get("taskId"),
        )
        if fail_task_handler is not None:
            return await fail_task_handler(payload)
        return {"outcome": "failed"}

    return ActivitySet(
        checkpoint_task=checkpoint_task,
        load_task_controls=load_task_controls,
        acknowledge_task_commands=acknowledge_task_commands,
        complete_chat=complete_chat,
        validate_outputs=validate_outputs,
        publish_outputs=publish_outputs,
        cancel_task=cancel_task,
        fail_task=fail_task,
        additional=additional,
    )
