"""Dispatch through the in-process runtime: durable before queued, and legacy tasks left where they are."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from eda_api.analysis.admission import AdmissionRejected
from eda_api.analysis.client import LocalAnalysisClient, RoutingTaskClient
from eda_api.analysis.supervisor import AnalysisSupervisor, SupervisorLimits, local_attempt_id
from eda_api.hosted_responses import HostedResponseAttempt, HostedResponseStatus
from eda_api.task_service import TaskService
from eda_contracts.tasks import TaskStatus
from eda_runtime_state.ledger import ExecutionLedgerStore, InMemoryLedgerContainer
from eda_runtime_state.messages import InMemoryMessageRepository
from eda_runtime_state.models import TaskPartition, TaskRecord
from eda_runtime_state.tasks import InMemoryRuntimeStateRepository

from .test_supervisor import Services

PARTITION = TaskPartition(
    tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
    owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
    session_id="ses_client0000000001",
)


async def _supervisor(repository: InMemoryRuntimeStateRepository, **limits: int) -> AnalysisSupervisor:
    supervisor = AnalysisSupervisor(
        services=Services(repository),
        tasks=repository,
        ledger=ExecutionLedgerStore(InMemoryLedgerContainer()),
        replica_id="replica-a",
        limits=SupervisorLimits(**limits),  # type: ignore[arg-type]
    )
    await supervisor._warmup_loop()  # pyright: ignore[reportPrivateUsage]
    return supervisor


async def _task(repository: InMemoryRuntimeStateRepository, suffix: str) -> TaskRecord:
    now = datetime.now(UTC)
    return await repository.create_task(
        TaskRecord(
            id=f"task_{suffix:0>12}",
            tenant_id=PARTITION.tenant_id,
            owner_object_id=PARTITION.owner_object_id,
            session_id=PARTITION.session_id,
            status=TaskStatus.PLANNING,
            checkpoint_sequence=0,
            command_sequence=0,
            applied_command_sequence=0,
            created_at=now,
            updated_at=now,
            expires_at=now + timedelta(days=1),
        ),
        f"request-{suffix:0>8}",
    )


class LegacyClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def start(self, task_id: str, *, user_identity: str, previous_response_id: str | None = None):
        raise AssertionError("no new task may be sent to the hosted agent")

    async def get(self, response_id: str, *, user_identity: str) -> HostedResponseAttempt:
        self.calls.append(("get", response_id))
        return HostedResponseAttempt(id=response_id, status=HostedResponseStatus.COMPLETED)

    async def cancel(self, response_id: str, *, user_identity: str) -> HostedResponseAttempt:
        self.calls.append(("cancel", response_id))
        return HostedResponseAttempt(id=response_id, status=HostedResponseStatus.CANCELLED)

    async def close(self) -> None:
        self.calls.append(("close", ""))


@pytest.mark.asyncio
async def test_start_persists_the_attempt_before_the_task_is_queued() -> None:
    repository = InMemoryRuntimeStateRepository()
    supervisor = await _supervisor(repository)
    task = await _task(repository, "d1")

    attempt = await LocalAnalysisClient(supervisor, repository).start(task.id, user_identity="usr_x")

    stored = await repository.resolve_task(task.id)
    assert stored is not None
    assert attempt.id == local_attempt_id(task.id) == stored.active_attempt_id
    assert attempt.status is HostedResponseStatus.QUEUED
    assert supervisor.queued_task_ids() == (task.id,)
    # The recovery index sees it too, so a crash before it runs cannot lose it.
    assert await repository.active_task_ids("run_") == [task.id]


@pytest.mark.asyncio
async def test_a_rejected_admission_persists_nothing() -> None:
    repository = InMemoryRuntimeStateRepository()
    supervisor = await _supervisor(repository, queue_depth=1)
    client = LocalAnalysisClient(supervisor, repository)
    await client.start((await _task(repository, "d2")).id, user_identity="usr_x")
    rejected = await _task(repository, "d3")

    with pytest.raises(AdmissionRejected):
        await client.start(rejected.id, user_identity="usr_x")

    stored = await repository.resolve_task(rejected.id)
    assert stored is not None and stored.active_attempt_id is None
    assert supervisor.queued_task_ids() == ("task_0000000000d2",)


@pytest.mark.asyncio
async def test_task_service_releases_the_dispatch_claim_when_admission_is_rejected() -> None:
    repository = InMemoryRuntimeStateRepository()
    messages = InMemoryMessageRepository()
    supervisor = await _supervisor(repository, queue_depth=1)
    await supervisor.stop()
    service = TaskService(repository, messages, RoutingTaskClient(LocalAnalysisClient(supervisor, repository)))
    message = await messages.append_user(PARTITION, "Summarise the rooms", "message-00000001")

    with pytest.raises(AdmissionRejected):
        await service.start_task(PARTITION, "idempotency-0001", message.id)

    [task_id] = [task_id async for task_id in _task_ids(repository)]
    stored = await repository.resolve_task(task_id)
    assert stored is not None
    assert not stored.initial_dispatch_claimed and stored.active_attempt_id is None


async def _task_ids(repository: InMemoryRuntimeStateRepository):
    for task_id in list(repository._tasks):  # pyright: ignore[reportPrivateUsage]
        yield task_id


@pytest.mark.asyncio
async def test_legacy_attempts_stay_with_the_hosted_agent_and_local_ones_never_reach_it() -> None:
    repository = InMemoryRuntimeStateRepository()
    supervisor = await _supervisor(repository)
    legacy = LegacyClient()
    router = RoutingTaskClient(LocalAnalysisClient(supervisor, repository), legacy=legacy)

    hosted = await router.get("resp_legacy000001", user_identity="usr_x")
    await router.cancel("resp_legacy000001", user_identity="usr_x")
    local = await router.get(local_attempt_id("task_000000000001"), user_identity="usr_x")
    await router.cancel(local_attempt_id("task_000000000001"), user_identity="usr_x")
    await router.close()

    assert hosted.status is HostedResponseStatus.COMPLETED
    # The supervisor owns its tasks' recovery, so reconciliation must never settle them from outside.
    assert local.status is HostedResponseStatus.IN_PROGRESS
    assert legacy.calls == [("get", "resp_legacy000001"), ("cancel", "resp_legacy000001"), ("close", "")]


@pytest.mark.asyncio
async def test_reconciliation_leaves_a_local_task_to_its_supervisor() -> None:
    repository = InMemoryRuntimeStateRepository()
    supervisor = await _supervisor(repository)
    service = TaskService(
        repository, InMemoryMessageRepository(), RoutingTaskClient(LocalAnalysisClient(supervisor, repository))
    )
    task = await _task(repository, "d4")
    await repository.replace_active_attempt(task.id, local_attempt_id(task.id), expected_attempt_id=None)
    current = await repository.resolve_task(task.id)
    assert current is not None

    reconciled = await service.reconcile_abandoned_task(current)

    assert reconciled.status is TaskStatus.PLANNING
