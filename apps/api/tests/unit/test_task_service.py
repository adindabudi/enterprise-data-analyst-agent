from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from eda_api.auth.models import Principal
from eda_api.hosted_responses import HostedResponseAttempt, HostedResponseStatus
from eda_api.task_service import SourceMessageNotFoundError, TaskService
from eda_contracts.tasks import TaskStatus
from eda_runtime_state.messages import InMemoryMessageRepository
from eda_runtime_state.models import TaskRecord
from eda_runtime_state.tasks import InMemoryRuntimeStateRepository


class FakeHostedClient:
    def __init__(self, repository: InMemoryRuntimeStateRepository | None = None) -> None:
        self.started: list[tuple[str, str, str | None]] = []
        self.cancelled: list[tuple[str, str]] = []
        self._repository = repository
        self.schedule_checks: list[tuple[str, bool]] = []
        self.states: dict[str, HostedResponseStatus] = {}
        self.state_error: Exception | None = None
        self._attempt_sequence = 0

    async def start(
        self,
        task_id: str,
        *,
        user_identity: str,
        previous_response_id: str | None = None,
    ) -> HostedResponseAttempt:
        if self._repository is not None:
            self.schedule_checks.append((task_id, await self._repository.resolve_task(task_id) is not None))
        self._attempt_sequence += 1
        response_id = f"resp_attempt{self._attempt_sequence:08d}"
        self.started.append((task_id, user_identity, previous_response_id))
        self.states[response_id] = HostedResponseStatus.QUEUED
        return HostedResponseAttempt(id=response_id, status=HostedResponseStatus.QUEUED)

    async def get(self, response_id: str, *, user_identity: str) -> HostedResponseAttempt:
        del user_identity
        if self.state_error is not None:
            raise self.state_error
        status = self.states.get(response_id, HostedResponseStatus.IN_PROGRESS)
        return HostedResponseAttempt(id=response_id, status=status)

    async def cancel(self, response_id: str, *, user_identity: str) -> HostedResponseAttempt:
        self.cancelled.append((response_id, user_identity))
        self.states[response_id] = HostedResponseStatus.CANCELLED
        return HostedResponseAttempt(id=response_id, status=HostedResponseStatus.CANCELLED)

    async def close(self) -> None:
        return None


@pytest.fixture
async def task() -> TaskRecord:
    now = datetime.now(UTC)
    record = TaskRecord(
        id="task_12345678",
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
        session_id="ses_1234567890abcdef",
        status=TaskStatus.COMPLETED,
        checkpoint_sequence=7,
        command_sequence=0,
        applied_command_sequence=0,
        final_message_id="msg_12345678",
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(days=1),
    )
    return record


@pytest.fixture
async def repository(task: TaskRecord) -> InMemoryRuntimeStateRepository:
    value = InMemoryRuntimeStateRepository()
    await value.create_task(task, "request-12345678")
    return value


@pytest.fixture
def owner() -> Principal:
    return Principal(
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
        audience=UUID("33333333-3333-3333-3333-333333333333"),
    )


@pytest.mark.asyncio
async def test_other_owner_task_lookup_is_hidden(repository: InMemoryRuntimeStateRepository, owner: Principal) -> None:
    service = TaskService(repository, InMemoryMessageRepository(), FakeHostedClient())
    other = owner.model_copy(update={"owner_object_id": UUID("44444444-4444-4444-4444-444444444444")})

    assert await service.get_owned_task(other, "task_12345678") is None


@pytest.mark.asyncio
async def test_terminal_snapshot_has_checkpoint_and_one_terminal_event(
    repository: InMemoryRuntimeStateRepository, owner: Principal, task: TaskRecord
) -> None:
    service = TaskService(repository, InMemoryMessageRepository(), FakeHostedClient())
    owned = await service.get_owned_task(owner, task.id)

    assert owned is not None
    entries = service.snapshot_events(owned)
    assert [entry.event.type.value for entry in entries] == ["task.checkpointed", "run.completed"]
    payload = entries[-1].event.model_dump(mode="json", by_alias=True)["payload"]
    assert payload["finalMessageId"] == task.final_message_id


@pytest.mark.asyncio
async def test_a_failed_hosted_response_stops_reporting_the_task_as_running() -> None:
    now = datetime.now(UTC)
    running = TaskRecord(
        id="task_87654321",
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
        session_id="ses_1234567890abcdef",
        status=TaskStatus.ANALYZING,
        checkpoint_sequence=1,
        command_sequence=0,
        applied_command_sequence=0,
        active_attempt_id="resp_running01",
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(days=1),
    )
    repository = InMemoryRuntimeStateRepository()
    await repository.create_task(running, "request-87654321")
    hosted = FakeHostedClient()
    hosted.states["resp_running01"] = HostedResponseStatus.FAILED
    service = TaskService(repository, InMemoryMessageRepository(), hosted)

    reconciled = await service.reconcile_abandoned_task(running)

    assert reconciled.status is TaskStatus.FAILED
    assert [entry.event.type.value for entry in service.snapshot_events(reconciled)] == [
        "task.checkpointed",
        "run.failed",
    ]


@pytest.mark.asyncio
async def test_a_live_hosted_response_leaves_the_task_running() -> None:
    now = datetime.now(UTC)
    running = TaskRecord(
        id="task_87654322",
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
        session_id="ses_1234567890abcdef",
        status=TaskStatus.ANALYZING,
        checkpoint_sequence=1,
        command_sequence=0,
        applied_command_sequence=0,
        active_attempt_id="resp_running02",
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(days=1),
    )
    repository = InMemoryRuntimeStateRepository()
    await repository.create_task(running, "request-87654322")
    hosted = FakeHostedClient()
    hosted.states["resp_running02"] = HostedResponseStatus.IN_PROGRESS
    service = TaskService(repository, InMemoryMessageRepository(), hosted)

    assert (await service.reconcile_abandoned_task(running)).status is TaskStatus.ANALYZING


@pytest.mark.asyncio
async def test_an_unreachable_hosted_endpoint_leaves_the_task_alone() -> None:
    now = datetime.now(UTC)
    running = TaskRecord(
        id="task_87654323",
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
        session_id="ses_1234567890abcdef",
        status=TaskStatus.ANALYZING,
        checkpoint_sequence=1,
        command_sequence=0,
        applied_command_sequence=0,
        active_attempt_id="resp_running03",
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(days=1),
    )
    repository = InMemoryRuntimeStateRepository()
    await repository.create_task(running, "request-87654323")
    hosted = FakeHostedClient()
    hosted.state_error = RuntimeError("hosted endpoint unreachable")
    service = TaskService(repository, InMemoryMessageRepository(), hosted)

    assert (await service.reconcile_abandoned_task(running)).status is TaskStatus.ANALYZING


@pytest.mark.asyncio
async def test_start_commits_product_state_before_starting_the_hosted_response(owner: Principal) -> None:
    repository = InMemoryRuntimeStateRepository()
    messages = InMemoryMessageRepository()
    hosted = FakeHostedClient(repository)
    service = TaskService(repository, messages, hosted)
    partition = task_partition(owner)
    source_message = await service.append_user_message(partition, "Analyze FY26 revenue", "request-message-1")

    task = await service.start_task(partition, "request-task-1", source_message.id)

    assert task.source_message_id == source_message.id
    assert await repository.get_owned_task(partition, task.id) is not None
    assert hosted.schedule_checks == [(task.id, True)]
    assert task.active_attempt_id == "resp_attempt00000001"
    assert hosted.started == [(task.id, _expected_hosted_user(task), None)]


@pytest.mark.asyncio
async def test_analyze_lane_schedules_even_for_a_greeting(owner: Principal) -> None:
    repository = InMemoryRuntimeStateRepository()
    messages = InMemoryMessageRepository()
    hosted = FakeHostedClient(repository)
    service = TaskService(repository, messages, hosted)
    partition = task_partition(owner)
    source_message = await service.append_user_message(partition, "halo", "request-greeting-message")

    task = await service.start_task(partition, "request-greeting-task", source_message.id)

    assert task.status is TaskStatus.PLANNING
    assert task.final_message_id is None
    assert hosted.started == [(task.id, _expected_hosted_user(task), None)]


@pytest.mark.asyncio
async def test_start_retry_reuses_the_committed_hosted_attempt(owner: Principal) -> None:
    repository = InMemoryRuntimeStateRepository()
    messages = InMemoryMessageRepository()
    partition = task_partition(owner)
    source_message = await messages.append_user(partition, "Analyze FY26 revenue", "request-message-duplicate")
    hosted = FakeHostedClient()
    service = TaskService(repository, messages, hosted)

    first = await service.start_task(partition, "request-task-duplicate", source_message.id)
    retried = await service.start_task(partition, "request-task-duplicate", source_message.id)

    assert retried == first
    assert len(hosted.started) == 1


@pytest.mark.asyncio
async def test_steering_persists_before_signal_and_retries_without_duplicate(
    repository: InMemoryRuntimeStateRepository, task: TaskRecord
) -> None:
    hosted = FakeHostedClient()
    service = TaskService(repository, InMemoryMessageRepository(), hosted)
    first = await service.steer(task.partition(), task.id, "check APAC", "command-12345678")
    retried = await service.steer(task.partition(), task.id, "check APAC", "command-12345678")

    assert first.id == retried.id
    assert hosted.started == []
    assert hosted.cancelled == []
    assert [command.sequence for command in await repository.pending_commands(task.id)] == [1]


@pytest.mark.asyncio
async def test_cancel_persists_before_signal_and_sets_cancellation_requested(
    owner: Principal,
) -> None:
    now = datetime.now(UTC)
    running = TaskRecord(
        id="task_cancel123",
        tenant_id=owner.tenant_id,
        owner_object_id=owner.owner_object_id,
        session_id="ses_1234567890abcdef",
        status=TaskStatus.ANALYZING,
        checkpoint_sequence=1,
        command_sequence=0,
        applied_command_sequence=0,
        active_attempt_id="resp_cancel123",
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(days=1),
    )
    repository = InMemoryRuntimeStateRepository()
    await repository.create_task(running, "request-cancel-123")
    hosted = FakeHostedClient()
    service = TaskService(repository, InMemoryMessageRepository(), hosted)

    first = await service.cancel(running.partition(), running.id, "command-cancel-1")
    retried = await service.cancel(running.partition(), running.id, "command-cancel-1")

    assert first.id == retried.id
    assert hosted.cancelled == [("resp_cancel123", _expected_hosted_user(running))]
    refreshed = await repository.get_owned_task(running.partition(), running.id)
    assert refreshed is not None
    assert refreshed.cancellation_requested is True


@pytest.mark.asyncio
async def test_start_rejects_missing_source_message(owner: Principal) -> None:
    repository = InMemoryRuntimeStateRepository()
    service = TaskService(repository, InMemoryMessageRepository(), FakeHostedClient())
    partition = task_partition(owner)

    with pytest.raises(SourceMessageNotFoundError):
        await service.start_task(partition, "request-task-missing", "msg_missing0001")


@pytest.mark.asyncio
async def test_start_rejects_source_message_from_other_partition(owner: Principal) -> None:
    repository = InMemoryRuntimeStateRepository()
    messages = InMemoryMessageRepository()
    service = TaskService(repository, messages, FakeHostedClient())
    owner_partition = task_partition(owner)
    other_partition = owner_partition.model_copy(
        update={"owner_object_id": UUID("44444444-4444-4444-4444-444444444444")}
    )
    other_message = await service.append_user_message(other_partition, "Other owner message", "request-message-2")

    with pytest.raises(SourceMessageNotFoundError):
        await service.start_task(owner_partition, "request-task-owner", other_message.id)


def task_partition(owner: Principal):
    from eda_runtime_state.models import TaskPartition

    return TaskPartition(
        tenant_id=owner.tenant_id,
        owner_object_id=owner.owner_object_id,
        session_id="ses_1234567890abcdef",
    )


def _expected_hosted_user(task: TaskRecord) -> str:
    material = f"{task.tenant_id}\0{task.owner_object_id}".encode()
    return f"usr_{hashlib.sha256(material).hexdigest()[:32]}"
