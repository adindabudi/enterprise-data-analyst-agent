# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from azure.cosmos.exceptions import CosmosHttpResponseError, CosmosResourceNotFoundError
from eda_contracts import ArtifactKind
from eda_contracts.controls import CommandKind
from eda_contracts.tasks import TaskStatus
from eda_runtime_state.models import OperationStatus, RequiredOutput, TaskRecord
from eda_runtime_state.tasks import CosmosRuntimeStateRepository, InMemoryRuntimeStateRepository, RuntimeStateConflict


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


@pytest.fixture
def repository() -> InMemoryRuntimeStateRepository:
    return InMemoryRuntimeStateRepository()


@pytest.mark.asyncio
async def test_locator_resolves_task_without_putting_claims_in_dts(
    repository: InMemoryRuntimeStateRepository, task: TaskRecord
) -> None:
    await repository.create_task(task, idempotency_key="request-12345678")

    resolved = await repository.resolve_task(task.id)

    assert resolved == task


@pytest.mark.asyncio
async def test_checkpoint_exact_replay_returns_committed_state(
    repository: InMemoryRuntimeStateRepository, task: TaskRecord
) -> None:
    await repository.create_task(task, idempotency_key="request-checkpoint-12345678")

    first = await repository.transition_task(task.id, TaskStatus.ANALYZING, expected_checkpoint=0)
    replay = await repository.transition_task(task.id, TaskStatus.ANALYZING, expected_checkpoint=0)

    assert replay == first
    assert replay.checkpoint_sequence == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["memory", "cosmos"])
async def test_output_contract_is_write_once(
    task: TaskRecord,
    backend: str,
) -> None:
    if backend == "cosmos":
        container = FakeCosmosTaskContainer(task)
        repository = CosmosRuntimeStateRepository(container, container)
    else:
        repository = InMemoryRuntimeStateRepository()
        await repository.create_task(task, idempotency_key="request-outputs-12345678")
    requirements = (RequiredOutput(kind=ArtifactKind.HTML), RequiredOutput(kind=ArtifactKind.XLSX))

    first = await repository.ensure_required_outputs(task.id, requirements)
    replay = await repository.ensure_required_outputs(task.id, (RequiredOutput(kind=ArtifactKind.HTML),))

    assert first.required_outputs == requirements
    assert replay.required_outputs == requirements
    restored = await repository.resolve_task(task.id)
    assert restored is not None and restored.required_outputs == requirements

    command = (
        await repository.append_command(
            task.partition(),
            task.id,
            CommandKind.STEER,
            "Only HTML now",
            "steer-output-12345678",
        )
        if backend == "memory"
        else None
    )
    if command is not None:
        steered = await repository.ensure_required_outputs(
            task.id,
            (RequiredOutput(kind=ArtifactKind.HTML),),
            through_sequence=command.sequence,
        )
        assert steered.required_outputs == (RequiredOutput(kind=ArtifactKind.HTML),)
        stale = await repository.ensure_required_outputs(task.id, requirements)
        assert stale.required_outputs == steered.required_outputs


@pytest.mark.asyncio
async def test_cosmos_output_contract_retries_unrelated_etag_conflict(task: TaskRecord) -> None:
    container = FakeCosmosTaskContainer(task)
    container.conflict_once = True
    repository = CosmosRuntimeStateRepository(container, container)

    result = await repository.ensure_required_outputs(task.id, (RequiredOutput(kind=ArtifactKind.HTML),))

    assert result.required_outputs == (RequiredOutput(kind=ArtifactKind.HTML),)


@pytest.mark.asyncio
async def test_commands_are_fifo_and_retry_is_idempotent(
    repository: InMemoryRuntimeStateRepository, task: TaskRecord
) -> None:
    await repository.create_task(task, idempotency_key="request-12345678")
    first = await repository.append_command(task.partition(), task.id, CommandKind.STEER, "check APAC", "command-1")
    retried = await repository.append_command(task.partition(), task.id, CommandKind.STEER, "check APAC", "command-1")
    second = await repository.append_command(task.partition(), task.id, CommandKind.CANCEL, None, "command-2")

    assert retried.id == first.id
    assert [item.sequence for item in await repository.pending_commands(task.id)] == [1, 2]
    assert [item.id for item in await repository.commands(task.id, [second.id, first.id])] == [second.id, first.id]
    assert second.sequence == 2


@pytest.mark.asyncio
async def test_cancel_command_sets_cancellation_requested_atomically(
    repository: InMemoryRuntimeStateRepository, task: TaskRecord
) -> None:
    await repository.create_task(task, idempotency_key="request-12345678")

    command = await repository.append_command(task.partition(), task.id, CommandKind.CANCEL, None, "cancel-1")
    updated = await repository.resolve_task(task.id)

    assert command.sequence == 1
    assert updated is not None
    assert updated.command_sequence == 1
    assert updated.cancellation_requested is True


@pytest.mark.asyncio
async def test_cancel_retry_is_idempotent_and_preserves_cancellation_requested(
    repository: InMemoryRuntimeStateRepository, task: TaskRecord
) -> None:
    await repository.create_task(task, idempotency_key="request-12345678")

    first = await repository.append_command(task.partition(), task.id, CommandKind.CANCEL, None, "cancel-1")
    retried = await repository.append_command(task.partition(), task.id, CommandKind.CANCEL, None, "cancel-1")
    updated = await repository.resolve_task(task.id)

    assert first.id == retried.id
    assert updated is not None
    assert updated.command_sequence == 1
    assert updated.cancellation_requested is True


class FakeBatchWorkspaceContainer:
    def __init__(self, task: TaskRecord) -> None:
        self._task_partition = task.partition().values()
        self._task = task.model_dump(mode="json", by_alias=True)
        self._commands: dict[str, dict[str, object]] = {}

    async def read_item(self, item: str, partition_key: list[str]) -> dict[str, object]:
        if partition_key != self._task_partition:
            raise CosmosResourceNotFoundError(message="not found")
        if item == self._task["id"]:
            return dict(self._task)
        command = self._commands.get(item)
        if command is None:
            raise CosmosResourceNotFoundError(message="not found")
        return dict(command)

    async def execute_item_batch(self, operations: list[Any], partition_key: list[str]) -> list[object]:
        if partition_key != self._task_partition:
            raise CosmosResourceNotFoundError(message="not found")
        replace = operations[0]
        create = operations[1]
        replace_body = dict(replace[1][1])
        etag = replace[2]["if_match_etag"]
        if etag != self._task.get("_etag"):
            raise ValueError("etag mismatch")
        replace_body["_etag"] = "next"
        self._task = replace_body
        command_body = dict(create[1][0])
        self._commands[str(command_body["id"])] = command_body
        return []


@pytest.mark.asyncio
async def test_cosmos_cancel_sets_cancellation_requested_atomically_and_is_idempotent(task: TaskRecord) -> None:
    container = FakeBatchWorkspaceContainer(task)
    repository = CosmosRuntimeStateRepository(container, container)  # type: ignore[arg-type]

    first = await repository.append_command(task.partition(), task.id, CommandKind.CANCEL, None, "cancel-1")
    retried = await repository.append_command(task.partition(), task.id, CommandKind.CANCEL, None, "cancel-1")
    updated = TaskRecord.model_validate(await container.read_item(task.id, task.partition().values()))

    assert first.id == retried.id
    assert updated.command_sequence == 1
    assert updated.cancellation_requested is True


@pytest.mark.asyncio
async def test_completed_operation_returns_prior_immutable_reference(
    repository: InMemoryRuntimeStateRepository, task: TaskRecord
) -> None:
    await repository.create_task(task, idempotency_key="request-12345678")
    operation = await repository.begin_operation(task, "sandbox-execute", {"script": "script-1"})
    await repository.complete_operation(task, operation.id, "result-7")

    replay = await repository.begin_operation(task, "sandbox-execute", {"script": "script-1"})

    assert replay.immutable_result_ref == "result-7"
    assert replay.status is OperationStatus.COMPLETED


@pytest.mark.asyncio
async def test_inmemory_active_sandbox_winner_and_idempotent_clear(
    repository: InMemoryRuntimeStateRepository, task: TaskRecord
) -> None:
    await repository.create_task(task, idempotency_key="request-12345678")

    winner = await repository.ensure_active_sandbox(task.id, "ds_proposed_1")
    second = await repository.ensure_active_sandbox(task.id, "ds_proposed_2")
    mismatch = await repository.clear_active_sandbox(task.id, "ds_other")
    cleared = await repository.clear_active_sandbox(task.id, winner)
    cleared_again = await repository.clear_active_sandbox(task.id, winner)

    assert winner == "ds_proposed_1"
    assert second == winner
    assert mismatch is not None
    assert mismatch.active_sandbox_id == winner
    assert cleared is not None
    assert cleared.active_sandbox_id is None
    assert cleared_again is not None
    assert cleared_again.active_sandbox_id is None


@pytest.mark.asyncio
async def test_inmemory_active_attempt_requires_the_expected_previous_attempt(
    repository: InMemoryRuntimeStateRepository, task: TaskRecord
) -> None:
    await repository.create_task(task, idempotency_key="request-12345678")

    first = await repository.replace_active_attempt(task.id, "resp_first123", expected_attempt_id=None)
    replay = await repository.replace_active_attempt(task.id, "resp_first123", expected_attempt_id=None)
    second = await repository.replace_active_attempt(
        task.id,
        "resp_second12",
        expected_attempt_id="resp_first123",
    )

    assert first.active_attempt_id == "resp_first123"
    assert replay == first
    assert second.active_attempt_id == "resp_second12"
    with pytest.raises(RuntimeStateConflict, match="active attempt assignment conflict"):
        await repository.replace_active_attempt(
            task.id,
            "resp_stale123",
            expected_attempt_id="resp_first123",
        )


class FakeCosmosTaskContainer:
    def __init__(self, task: TaskRecord) -> None:
        partition = task.partition().values()
        task_document = task.model_dump(mode="json", by_alias=True)
        task_document["_etag"] = "etag-1"
        locator_ttl = max(1, int((task.expires_at - datetime.now(UTC)).total_seconds()))
        self._task_partition = partition
        self._task_id = task.id
        self._task = task_document
        self._locator = {
            "id": task.id,
            "recordType": "runtimeLocator",
            "tenantId": str(task.tenant_id),
            "ownerObjectId": str(task.owner_object_id),
            "sessionId": task.session_id,
            "expiresAt": task.expires_at,
            "ttl": locator_ttl,
        }
        self._etag_counter = 1
        self.conflict_once = False
        self.conflict_winner: str | None = None
        self.conflict_attempt_winner: str | None = None

    async def read_item(self, item: str, partition_key: list[str] | str) -> dict[str, Any]:
        if partition_key == self._task_partition and item == self._task_id:
            return dict(self._task)
        if partition_key == self._task_id and item == self._task_id:
            return dict(self._locator)
        raise CosmosResourceNotFoundError(message="not found")

    async def replace_item(
        self,
        item: str,
        body: dict[str, Any],
        etag: str,
        match_condition: Any,
    ) -> dict[str, Any]:
        del match_condition
        if item != self._task_id:
            raise CosmosResourceNotFoundError(message="not found")
        current_etag = str(self._task.get("_etag", ""))
        if self.conflict_once:
            self.conflict_once = False
            if self.conflict_winner is not None:
                self._task["activeSandboxId"] = self.conflict_winner
            if self.conflict_attempt_winner is not None:
                self._task["activeAttemptId"] = self.conflict_attempt_winner
            self._etag_counter += 1
            self._task["_etag"] = f"etag-{self._etag_counter}"
            raise CosmosHttpResponseError(status_code=412, message="precondition failed")
        if etag != current_etag:
            raise CosmosHttpResponseError(status_code=412, message="precondition failed")
        self._etag_counter += 1
        next_document = dict(body)
        next_document["_etag"] = f"etag-{self._etag_counter}"
        self._task = next_document
        return dict(self._task)


@pytest.mark.asyncio
async def test_cosmos_ensure_active_sandbox_returns_conflict_winner(task: TaskRecord) -> None:
    container = FakeCosmosTaskContainer(task)
    container.conflict_once = True
    container.conflict_winner = "ds_winner_12345678"
    repository = CosmosRuntimeStateRepository(container, container)  # type: ignore[arg-type]

    winner = await repository.ensure_active_sandbox(task.id, "ds_proposed_12345678")

    assert winner == "ds_winner_12345678"


@pytest.mark.asyncio
async def test_cosmos_ensure_active_sandbox_raises_when_conflict_has_no_winner(task: TaskRecord) -> None:
    container = FakeCosmosTaskContainer(task)
    container.conflict_once = True
    container.conflict_winner = None
    repository = CosmosRuntimeStateRepository(container, container)  # type: ignore[arg-type]

    with pytest.raises(RuntimeStateConflict):
        await repository.ensure_active_sandbox(task.id, "ds_proposed_12345678")


@pytest.mark.asyncio
async def test_cosmos_clear_active_sandbox_is_expected_match_only(task: TaskRecord) -> None:
    seeded = task.model_copy(update={"active_sandbox_id": "ds_active_12345678"})
    container = FakeCosmosTaskContainer(seeded)
    repository = CosmosRuntimeStateRepository(container, container)  # type: ignore[arg-type]

    mismatch = await repository.clear_active_sandbox(seeded.id, "ds_other")
    assert mismatch is not None
    assert mismatch.active_sandbox_id == "ds_active_12345678"

    cleared = await repository.clear_active_sandbox(seeded.id, "ds_active_12345678")
    assert cleared is not None
    assert cleared.active_sandbox_id is None


@pytest.mark.asyncio
async def test_cosmos_active_attempt_compare_and_set_is_idempotent(task: TaskRecord) -> None:
    container = FakeCosmosTaskContainer(task)
    repository = CosmosRuntimeStateRepository(container, container)  # type: ignore[arg-type]

    first = await repository.replace_active_attempt(task.id, "resp_first123", expected_attempt_id=None)
    replay = await repository.replace_active_attempt(task.id, "resp_first123", expected_attempt_id=None)
    second = await repository.replace_active_attempt(
        task.id,
        "resp_second12",
        expected_attempt_id="resp_first123",
    )

    assert first.active_attempt_id == "resp_first123"
    assert replay.active_attempt_id == "resp_first123"
    assert second.active_attempt_id == "resp_second12"


async def dispatch_repository(task: TaskRecord, backend: str):
    if backend == "cosmos":
        container = FakeCosmosTaskContainer(task)
        return CosmosRuntimeStateRepository(container, container)
    repository = InMemoryRuntimeStateRepository()
    await repository.create_task(task, "dispatch-test-key")
    return repository


@pytest.mark.parametrize("backend", ["memory", "cosmos"])
@pytest.mark.asyncio
async def test_initial_dispatch_release_is_conditional_and_allows_retry(task: TaskRecord, backend: str) -> None:
    repository = await dispatch_repository(task, backend)

    assert await repository.claim_initial_dispatch(task.id)
    assert not await repository.claim_initial_dispatch(task.id)
    assert await repository.release_initial_dispatch(task.id)
    assert not await repository.release_initial_dispatch(task.id)
    assert await repository.claim_initial_dispatch(task.id)
    await repository.replace_active_attempt(task.id, "resp_dispatched01", expected_attempt_id=None)
    assert not await repository.release_initial_dispatch(task.id)
    assert not await repository.claim_initial_dispatch(task.id)


@pytest.mark.parametrize("backend", ["memory", "cosmos"])
@pytest.mark.asyncio
async def test_initial_dispatch_cannot_claim_terminal_tasks(task: TaskRecord, backend: str) -> None:
    repository = await dispatch_repository(task.model_copy(update={"status": TaskStatus.FAILED}), backend)

    assert not await repository.claim_initial_dispatch(task.id)


@pytest.mark.parametrize("backend", ["memory", "cosmos"])
@pytest.mark.asyncio
async def test_abandoned_initial_dispatch_cannot_attach_a_late_attempt(task: TaskRecord, backend: str) -> None:
    seeded = task.model_copy(update={"initial_dispatch_claimed": True})
    repository = await dispatch_repository(seeded, backend)
    failed = await repository.fail_abandoned_initial_dispatch(
        task.id, stale_before=task.updated_at + timedelta(seconds=1)
    )

    assert failed.status is TaskStatus.FAILED
    assert failed.initial_dispatch_claimed
    assert failed.checkpoint_sequence == task.checkpoint_sequence + 1
    assert await repository.fail_abandoned_initial_dispatch(task.id, stale_before=failed.updated_at) == failed
    with pytest.raises(RuntimeStateConflict):
        await repository.replace_active_attempt(task.id, "resp_late_acknowledgement", expected_attempt_id=None)


@pytest.mark.parametrize("backend", ["memory", "cosmos"])
@pytest.mark.parametrize(
    "updates", [{}, {"active_attempt_id": "resp_already_attached"}, {"status": TaskStatus.ANALYZING}]
)
@pytest.mark.asyncio
async def test_initial_dispatch_recovery_preserves_recent_or_running_tasks(
    task: TaskRecord, backend: str, updates
) -> None:
    seeded = task.model_copy(update={"initial_dispatch_claimed": True, **updates})
    repository = await dispatch_repository(seeded, backend)
    before = await repository.resolve_task(task.id)
    cutoff = task.updated_at if not updates else task.updated_at + timedelta(minutes=20)

    assert await repository.fail_abandoned_initial_dispatch(task.id, stale_before=cutoff) == before


@pytest.mark.asyncio
async def test_cosmos_initial_dispatch_recovery_preserves_a_concurrent_attempt(task: TaskRecord) -> None:
    container = FakeCosmosTaskContainer(task.model_copy(update={"initial_dispatch_claimed": True}))
    container.conflict_once = True
    container.conflict_attempt_winner = "resp_concurrent_attempt"
    repository = CosmosRuntimeStateRepository(container, container)

    recovered = await repository.fail_abandoned_initial_dispatch(
        task.id, stale_before=task.updated_at + timedelta(seconds=1)
    )

    assert recovered.active_attempt_id == "resp_concurrent_attempt"
    assert recovered.status is TaskStatus.PLANNING
    assert recovered.initial_dispatch_claimed


@pytest.mark.asyncio
async def test_cosmos_initial_dispatch_release_leaves_claim_intact_on_conflict(task: TaskRecord) -> None:
    container = FakeCosmosTaskContainer(task.model_copy(update={"initial_dispatch_claimed": True}))
    container.conflict_once = True
    repository = CosmosRuntimeStateRepository(container, container)

    with pytest.raises(RuntimeStateConflict):
        await repository.release_initial_dispatch(task.id)
    current = await repository.resolve_task(task.id)
    assert current is not None and current.initial_dispatch_claimed
