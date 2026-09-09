from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

import pytest
from eda_contracts.controls import CommandKind
from eda_contracts.tasks import TaskStatus
from eda_runtime_state.models import TaskRecord
from eda_runtime_state.tasks import InMemoryRuntimeStateRepository
from eda_worker.activities import create_activities
from eda_worker.finalization import CoreTaskFinalizer

MetricPoints = Callable[[str], list[tuple[float, dict[str, object]]]]


class NullEventStore:
    async def append(self, draft: object) -> None:
        del draft


@pytest.fixture
def task() -> TaskRecord:
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
async def test_a_finished_run_is_counted_where_the_completion_objective_reads_it(
    task: TaskRecord, metric_points: MetricPoints
) -> None:
    repository = InMemoryRuntimeStateRepository()
    await repository.create_task(task, "request-12345678")
    activities = create_activities(repository, NullEventStore())

    await activities.checkpoint_task({"taskId": task.id, "status": TaskStatus.ANALYZING.value, "expectedCheckpoint": 0})
    await activities.checkpoint_task({"taskId": task.id, "status": TaskStatus.COMPLETED.value, "expectedCheckpoint": 1})

    assert metric_points("eda.task.completed") == [(1, {})]


@pytest.mark.asyncio
async def test_a_failed_run_is_counted_where_the_alert_reads_it(task: TaskRecord, metric_points: MetricPoints) -> None:
    repository = InMemoryRuntimeStateRepository()
    await repository.create_task(task, "request-12345678")
    activities = create_activities(repository, NullEventStore())

    await activities.checkpoint_task({"taskId": task.id, "status": TaskStatus.FAILED.value, "expectedCheckpoint": 0})

    # The deployed worker-task-failed alert queries this name; nothing emitted it before.
    assert metric_points("eda.task.failed") == [(1, {})]


@pytest.mark.asyncio
async def test_cancellation_latency_is_measured_from_the_request_not_the_activity(
    task: TaskRecord, metric_points: MetricPoints
) -> None:
    repository = InMemoryRuntimeStateRepository()
    await repository.create_task(task, "request-12345678")
    await repository.append_command(task.partition(), task.id, CommandKind.CANCEL, None, "command-1")
    activities = create_activities(repository, NullEventStore())

    await activities.cancel_task({"taskId": task.id, "reason": "before_chat"})

    assert len(metric_points("eda.cancel.ack")) == 1


@pytest.mark.asyncio
async def test_measuring_cancellation_never_stops_the_cancellation(
    task: TaskRecord, caplog: pytest.LogCaptureFixture
) -> None:
    class BrokenCommands(InMemoryRuntimeStateRepository):
        async def pending_commands(self, task_id: str) -> Any:
            raise RuntimeError("cosmos is unreachable")

    repository = BrokenCommands()
    await repository.create_task(task, "request-12345678")
    activities = create_activities(repository, NullEventStore())

    assert await activities.cancel_task({"taskId": task.id, "reason": "before_chat"}) == {"outcome": "cancelled"}
    assert "cancellation latency could not be measured" in caplog.text


@pytest.mark.asyncio
async def test_publication_records_whether_it_was_structurally_validated(metric_points: MetricPoints) -> None:
    class NoArtifacts:
        async def published_refs(self, task_id: str) -> tuple[Any, ...]:
            del task_id
            return ()

    finalizer = CoreTaskFinalizer(
        cast(Any, None),
        NoArtifacts(),
        cast(Any, None),
        cast(Any, None),
    )

    await finalizer.validate_outputs({"taskId": "task_12345678"})

    assert metric_points("eda.publish.structural") == [(0, {})]
