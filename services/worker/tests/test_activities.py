from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from eda_contracts.controls import CommandKind
from eda_contracts.tasks import TaskStatus
from eda_runtime_state.models import TaskRecord
from eda_runtime_state.tasks import InMemoryRuntimeStateRepository
from eda_worker.activities import create_activities


class FailingEventStore:
    async def append(self, draft) -> None:
        del draft
        raise ConnectionError("redis unavailable")


class RecordingEventStore:
    def __init__(self) -> None:
        self.drafts: list[object] = []

    async def append(self, draft) -> None:
        self.drafts.append(draft)


@pytest.fixture
async def task() -> TaskRecord:
    now = datetime.now(UTC)
    return TaskRecord(
        id="task_12345678",
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
        session_id="ses_1234567890abcdef",
        status=TaskStatus.PLANNING,
        checkpoint_sequence=0,
        command_sequence=0,
        applied_command_sequence=0,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(days=1),
    )


@pytest.mark.asyncio
async def test_checkpoint_persists_before_best_effort_live_event(task: TaskRecord) -> None:
    repository = InMemoryRuntimeStateRepository()
    await repository.create_task(task, "request-12345678")
    activities = create_activities(repository, FailingEventStore())

    result = await activities.checkpoint_task(
        {"taskId": task.id, "status": TaskStatus.ACQUIRING_DATA.value, "expectedCheckpoint": 0}
    )

    assert json.loads(json.dumps(result)) == {
        "checkpointSequence": 1,
        "status": TaskStatus.ACQUIRING_DATA.value,
    }
    updated = await repository.resolve_task(task.id)
    assert updated is not None
    assert updated.status is TaskStatus.ACQUIRING_DATA
    assert updated.checkpoint_sequence == 1


@pytest.mark.asyncio
async def test_control_snapshot_contains_fifo_ids_not_command_text(task: TaskRecord) -> None:
    repository = InMemoryRuntimeStateRepository()
    await repository.create_task(task, "request-12345678")
    await repository.append_command(task.partition(), task.id, CommandKind.STEER, "secret guidance", "command-1")
    activities = create_activities(repository, FailingEventStore())

    controls = await activities.load_task_controls({"taskId": task.id})

    assert controls["pendingCommandIds"]
    assert "secret guidance" not in str(controls)


@pytest.mark.asyncio
async def test_chat_control_snapshot_skips_command_query(task: TaskRecord) -> None:
    class Repository(InMemoryRuntimeStateRepository):
        async def pending_commands(self, task_id: str):
            raise AssertionError(f"chat must not query command inbox for {task_id}")

    repository = Repository()
    await repository.create_task(task, "request-chat-controls-12345678")
    activities = create_activities(repository, FailingEventStore())

    controls = await activities.load_task_controls({"taskId": task.id, "includeCommands": False})

    assert controls == {
        "cancellationRequested": False,
        "pendingCommandIds": [],
        "highestCommandSequence": 0,
        "authResumed": False,
    }


@pytest.mark.asyncio
async def test_terminal_checkpoint_emits_checkpoint_then_terminal_event(task: TaskRecord) -> None:
    repository = InMemoryRuntimeStateRepository()
    publishing = task.model_copy(update={"status": TaskStatus.PUBLISHING})
    await repository.create_task(publishing, "request-terminal-12345678")
    events = RecordingEventStore()
    activities = create_activities(repository, events)

    await activities.checkpoint_task({"taskId": task.id, "status": TaskStatus.COMPLETED.value, "expectedCheckpoint": 0})

    assert [draft.type for draft in events.drafts] == ["task.checkpointed", "run.completed"]


@pytest.mark.asyncio
async def test_analyzing_checkpoint_emits_thinking_feedback(task: TaskRecord) -> None:
    repository = InMemoryRuntimeStateRepository()
    await repository.create_task(task, "request-thinking-12345678")
    events = RecordingEventStore()
    activities = create_activities(repository, events)

    await activities.checkpoint_task({"taskId": task.id, "status": TaskStatus.ANALYZING.value, "expectedCheckpoint": 0})

    assert [draft.type for draft in events.drafts] == ["task.checkpointed", "analysis_progress"]
    assert events.drafts[1].payload == {
        "milestone": "Agent is thinking",
        "detail": "Preparing tools and response.",
        "state": "running",
    }


@pytest.mark.asyncio
async def test_a_failed_task_records_why_it_failed(caplog: pytest.LogCaptureFixture) -> None:
    activities = create_activities(
        repository=InMemoryRuntimeStateRepository(),
        event_store=RecordingEventStore(),
    )

    with caplog.at_level(logging.ERROR):
        outcome = await activities.fail_task(
            {
                "taskId": "task_12345678",
                "failureCode": "chat_failed",
                "diagnosticRef": "diag:task_12345678",
            }
        )

    assert outcome == {"outcome": "failed"}
    assert "chat_failed" in caplog.text
    assert "diag:task_12345678" in caplog.text
