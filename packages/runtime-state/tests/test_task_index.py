# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""Query evidence recorded on a task, and the index the supervisor recovers from."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from eda_contracts.tasks import TaskStatus
from eda_runtime_state.models import QueryResultRef, TaskRecord
from eda_runtime_state.tasks import CosmosRuntimeStateRepository, InMemoryRuntimeStateRepository

from .test_tasks import FakeCosmosTaskContainer


def _task(task_id: str = "task_12345678", *, attempt: str | None = None, status: TaskStatus = TaskStatus.PLANNING):
    now = datetime.now(UTC)
    return TaskRecord(
        id=task_id,
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
        session_id="ses_1234567890abcdef",
        status=status,
        checkpoint_sequence=0,
        command_sequence=0,
        applied_command_sequence=0,
        active_attempt_id=attempt,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(days=1),
    )


def _ref(index: int) -> QueryResultRef:
    return QueryResultRef(
        artifact_id=f"artifact-query{index:04d}",
        version=1,
        kind="query_result",
        sha256="a" * 64,
        display_name=f"query-result-{index}.json",
        query="MATCH (r:rooms) RETURN count(*) AS total",
        query_sha256="b" * 64,
        row_count=1,
        source_alias="lamna",
        executed_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_query_results_are_appended_once_and_bounded_at_ten() -> None:
    repository = InMemoryRuntimeStateRepository()
    await repository.create_task(_task(), "request-12345678")

    assert await repository.append_query_result("task_12345678", _ref(0))
    assert await repository.append_query_result("task_12345678", _ref(0))
    for index in range(1, 10):
        assert await repository.append_query_result("task_12345678", _ref(index))
    # The eleventh is refused rather than silently evicting evidence the model already cited.
    assert not await repository.append_query_result("task_12345678", _ref(10))

    stored = await repository.resolve_task("task_12345678")
    assert stored is not None
    assert [ref.artifact_id for ref in stored.query_results] == [_ref(index).artifact_id for index in range(10)]


@pytest.mark.asyncio
async def test_cosmos_append_retries_a_checkpoint_that_raced_it() -> None:
    container = FakeCosmosTaskContainer(_task())
    container.conflict_once = True
    repository = CosmosRuntimeStateRepository(container, container)  # type: ignore[arg-type]

    assert await repository.append_query_result("task_12345678", _ref(0))

    stored = await repository.resolve_task("task_12345678")
    assert stored is not None
    assert [ref.artifact_id for ref in stored.query_results] == [_ref(0).artifact_id]


@pytest.mark.asyncio
async def test_active_task_index_lists_only_live_tasks_of_the_requested_engine() -> None:
    repository = InMemoryRuntimeStateRepository()
    await repository.create_task(_task("task_unified01", attempt="run_aaaaaaaa"), "request-00000001")
    await repository.create_task(_task("task_legacy001", attempt="resp_bbbbbbbb"), "request-00000002")
    await repository.create_task(_task("task_undispatched"), "request-00000003")
    await repository.create_task(
        _task("task_finished1", attempt="run_cccccccc", status=TaskStatus.COMPLETED), "request-00000004"
    )

    assert await repository.active_task_ids("run_") == ["task_unified01"]
