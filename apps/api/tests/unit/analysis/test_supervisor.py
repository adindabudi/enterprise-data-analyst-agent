# pyright: reportPrivateUsage=false
"""The in-process supervisor: admission, exclusivity, cancellation, budgets and recovery.

Every case runs the real lifecycle and the real ledger over in-memory stores; only
the model pass is scripted, so what is checked is what the supervisor decides.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from eda_api.analysis.admission import AdmissionRejected
from eda_api.analysis.supervisor import (
    BUDGET_FAILURE,
    QUEUE_TIMEOUT_FAILURE,
    RUNTIME_ERROR_FAILURE,
    AnalysisSupervisor,
    SupervisorLimits,
    local_attempt_id,
)
from eda_contracts.controls import CommandKind
from eda_contracts.tasks import TaskStatus
from eda_runtime_state.ledger import ExecutionLedgerStore, InMemoryLedgerContainer
from eda_runtime_state.models import TaskRecord
from eda_runtime_state.tasks import InMemoryRuntimeStateRepository
from eda_worker.contracts import ControlSnapshot

TENANT = UUID("11111111-1111-1111-1111-111111111111")
OWNER = UUID("22222222-2222-2222-2222-222222222222")
OTHER_OWNER = UUID("33333333-3333-3333-3333-333333333333")


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 9, 23, 8, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


class Services:
    """The lifecycle's services over the in-memory task store, with a scripted model pass."""

    def __init__(self, repository: InMemoryRuntimeStateRepository) -> None:
        self.repository = repository
        self.calls: list[tuple[object, ...]] = []
        self.release = asyncio.Event()
        self.release.set()
        self.started: dict[str, asyncio.Event] = {}
        self.pass_error: BaseException | None = None
        self.warmup_failures = 0

    def started_event(self, task_id: str) -> asyncio.Event:
        return self.started.setdefault(task_id, asyncio.Event())

    async def warmup(self) -> None:
        if self.warmup_failures:
            self.warmup_failures -= 1
            raise RuntimeError("runtime not ready")

    async def reset_execution(self, task_id: str) -> None:
        self.calls.append(("reset", task_id))

    async def task(self, task_id: str) -> TaskRecord | None:
        return await self.repository.resolve_task(task_id)

    async def controls(self, task_id: str) -> ControlSnapshot:
        task = await self.repository.resolve_task(task_id)
        commands = await self.repository.pending_commands(task_id)
        return ControlSnapshot(
            cancellationRequested=bool(task and task.cancellation_requested),
            pendingCommandIds=tuple(command.id for command in commands if command.kind is CommandKind.STEER),
            highestCommandSequence=max((command.sequence for command in commands), default=0),
        )

    async def checkpoint(self, task_id: str, status: TaskStatus, expected_checkpoint: int) -> TaskRecord:
        self.calls.append(("checkpoint", task_id, status))
        return await self.repository.transition_task(task_id, status, expected_checkpoint)

    async def run_analysis(
        self, task_id: str, pending_command_ids: tuple[str, ...], repair_feedback: str | None = None
    ) -> str:
        del pending_command_ids, repair_feedback
        self.calls.append(("run", task_id))
        self.started_event(task_id).set()
        await self.release.wait()
        if self.pass_error is not None:
            raise self.pass_error
        return f"answer for {task_id}"

    async def acknowledge(self, task_id: str, through_sequence: int) -> None:
        await self.repository.acknowledge_commands(task_id, through_sequence)

    async def complete(self, task_id: str, text: str) -> str:
        del text
        message_id = f"msg_final_{task_id[-8:]}"
        await self.repository.set_final_message(task_id, message_id)
        self.calls.append(("complete", task_id))
        return message_id

    async def cancel(self, task_id: str) -> None:
        self.calls.append(("cancel", task_id))

    async def fail(self, task_id: str, failure_code: str) -> None:
        self.calls.append(("fail", task_id, failure_code))

    def runs(self, task_id: str) -> int:
        return sum(1 for call in self.calls if call[:2] == ("run", task_id))


class Harness:
    def __init__(self, *, limits: SupervisorLimits | None = None, replica_id: str = "replica-a") -> None:
        self.clock = Clock()
        self.repository = InMemoryRuntimeStateRepository()
        self.container = InMemoryLedgerContainer()
        self.ledger = ExecutionLedgerStore(self.container, clock=self.clock)
        self.services = Services(self.repository)
        self.supervisor = self.make_supervisor(replica_id, limits=limits)

    def make_supervisor(self, replica_id: str, *, limits: SupervisorLimits | None = None) -> AnalysisSupervisor:
        return AnalysisSupervisor(
            services=self.services,
            tasks=self.repository,
            ledger=self.ledger,
            replica_id=replica_id,
            limits=limits or SupervisorLimits(),
            sleep=_never,
        )

    async def create(
        self,
        suffix: str,
        *,
        session: str | None = None,
        owner: UUID = OWNER,
        created_at: datetime | None = None,
    ) -> TaskRecord:
        created = created_at or self.clock.now
        task = TaskRecord(
            id=f"task_{suffix:0>12}",
            tenant_id=TENANT,
            owner_object_id=owner,
            session_id=session or f"ses_{suffix:0>16}",
            status=TaskStatus.PLANNING,
            checkpoint_sequence=0,
            command_sequence=0,
            applied_command_sequence=0,
            created_at=created,
            updated_at=created,
            expires_at=created + timedelta(days=30),
        )
        return await self.repository.create_task(task, f"request-{suffix:0>8}")

    async def submit(self, suffix: str, **kwargs: object) -> TaskRecord:
        task = await self.create(suffix, **kwargs)  # type: ignore[arg-type]
        return await self.admit(self.supervisor, task)

    @staticmethod
    async def admit_into(supervisor: AnalysisSupervisor, repository: InMemoryRuntimeStateRepository, task: TaskRecord):
        reservation = supervisor.reserve(f"{task.tenant_id}:{task.owner_object_id}")
        attempt = local_attempt_id(task.id)
        await repository.replace_active_attempt(task.id, attempt, expected_attempt_id=None)
        supervisor.enqueue(reservation, task_id=task.id, session_id=task.session_id, attempt_id=attempt)
        return await repository.resolve_task(task.id)

    async def admit(self, supervisor: AnalysisSupervisor, task: TaskRecord) -> TaskRecord:
        admitted = await self.admit_into(supervisor, self.repository, task)
        assert admitted is not None
        return admitted

    async def ready(self, supervisor: AnalysisSupervisor | None = None) -> None:
        target = supervisor or self.supervisor
        await target._warmup_loop()
        assert target.state == "ready"

    async def status(self, task_id: str) -> TaskStatus:
        task = await self.repository.resolve_task(task_id)
        assert task is not None
        return task.status


async def _never(seconds: float) -> None:
    del seconds
    await asyncio.Event().wait()


async def _settle(supervisor: AnalysisSupervisor) -> None:
    """Let launched jobs run to completion."""
    for _ in range(50):
        jobs = [running.job for running in supervisor._running.values() if running.job is not None]
        if not jobs:
            return
        await asyncio.wait(jobs, timeout=1)
    raise AssertionError("jobs did not finish")


async def _eventually(condition: Callable[[], Awaitable[bool]]) -> None:
    for _ in range(200):
        if await condition():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition was never met")


@pytest.mark.asyncio
async def test_an_admitted_task_runs_once_and_completes_with_its_claim_released() -> None:
    harness = Harness()
    await harness.ready()
    task = await harness.submit("a1")

    await harness.supervisor._dispatch()
    await _settle(harness.supervisor)

    assert await harness.status(task.id) is TaskStatus.COMPLETED
    assert harness.services.runs(task.id) == 1
    assert (await harness.ledger.snapshot()).entries == ()
    assert harness.supervisor.running_task_ids() == ()


@pytest.mark.asyncio
async def test_admission_is_bounded_per_owner_and_by_queue_depth() -> None:
    harness = Harness(limits=SupervisorLimits(queue_depth=3, per_owner_limit=2))
    await harness.ready()
    await harness.submit("q1")
    await harness.submit("q2")

    with pytest.raises(AdmissionRejected) as owner_limit:
        harness.supervisor.reserve(f"{TENANT}:{OWNER}")
    assert owner_limit.value.reason == "owner_limit"

    await harness.submit("q3", owner=OTHER_OWNER)
    with pytest.raises(AdmissionRejected) as full:
        harness.supervisor.reserve(f"{TENANT}:{OTHER_OWNER}")
    assert full.value.reason == "queue_full"


@pytest.mark.asyncio
async def test_a_runtime_that_has_not_warmed_up_takes_no_work() -> None:
    harness = Harness()

    with pytest.raises(AdmissionRejected) as starting:
        harness.supervisor.reserve(f"{TENANT}:{OWNER}")
    assert starting.value.reason == "runtime_unavailable"


@pytest.mark.asyncio
async def test_a_blocked_runtime_rejects_new_work_instead_of_queueing_it() -> None:
    harness = Harness()
    harness.services.warmup_failures = 1
    warmup = asyncio.create_task(harness.supervisor._warmup_loop())
    await _eventually(lambda: _true_when(harness.supervisor.state == "blocked"))

    with pytest.raises(AdmissionRejected) as rejected:
        harness.supervisor.reserve(f"{TENANT}:{OWNER}")
    assert rejected.value.reason == "runtime_unavailable"
    warmup.cancel()


async def _true_when(value: bool) -> bool:
    return value


@pytest.mark.asyncio
async def test_a_conversation_never_has_two_tasks_executing_at_once() -> None:
    harness = Harness()
    await harness.ready()
    harness.services.release.clear()
    first = await harness.submit("c1", session="ses_shared0000000001")
    second = await harness.submit("c2", session="ses_shared0000000001")

    await harness.supervisor._dispatch()
    await harness.services.started_event(first.id).wait()

    assert harness.supervisor.running_task_ids() == (first.id,)
    assert harness.supervisor.queued_task_ids() == (second.id,)

    harness.services.release.set()
    await _settle(harness.supervisor)
    await harness.supervisor._dispatch()
    await _settle(harness.supervisor)

    assert await harness.status(second.id) is TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_replica_and_deployment_limits_hold_across_replicas() -> None:
    limits = SupervisorLimits(max_active_per_replica=2, deployment_limit=3)
    harness = Harness(limits=limits)
    other = harness.make_supervisor("replica-b", limits=limits)
    await harness.ready()
    await harness.ready(other)
    harness.services.release.clear()
    local = [await harness.submit(f"l{index}") for index in range(3)]
    remote = [await harness.admit(other, await harness.create(f"r{index}")) for index in range(2)]

    await harness.supervisor._dispatch()
    await other._dispatch()

    assert len(harness.supervisor.running_task_ids()) == 2
    assert len(other.running_task_ids()) == 1
    live = (await harness.ledger.snapshot()).live(harness.clock())
    assert len(live) == 3
    harness.services.release.set()
    await _settle(harness.supervisor)
    await _settle(other)
    assert {task.id for task in local} >= set(harness.supervisor.queued_task_ids())
    assert remote


@pytest.mark.asyncio
async def test_owners_are_served_in_turn_rather_than_in_arrival_order() -> None:
    limits = SupervisorLimits(max_active_per_replica=1, deployment_limit=1)
    harness = Harness(limits=limits)
    await harness.ready()
    harness.services.release.clear()
    busy = [await harness.submit(f"o{index}") for index in range(3)]
    newcomer = await harness.submit("n0", owner=OTHER_OWNER)

    await harness.supervisor._dispatch()
    assert harness.supervisor.running_task_ids() == (busy[0].id,)
    harness.services.release.set()
    await _settle(harness.supervisor)
    harness.services.release.clear()
    await harness.supervisor._dispatch()

    assert harness.supervisor.running_task_ids() == (newcomer.id,)
    harness.services.release.set()
    await _settle(harness.supervisor)


@pytest.mark.asyncio
async def test_cancelling_a_queued_task_settles_it_without_running_it() -> None:
    harness = Harness(limits=SupervisorLimits(max_active_per_replica=1, deployment_limit=1))
    await harness.ready()
    harness.services.release.clear()
    running = await harness.submit("x1")
    queued = await harness.submit("x2")
    await harness.supervisor._dispatch()
    await harness.services.started_event(running.id).wait()

    await harness.repository.append_command(queued.partition(), queued.id, CommandKind.CANCEL, None, "cancel-x2")
    harness.supervisor.request_cancel(local_attempt_id(queued.id))
    await harness.supervisor._dispatch()

    assert await harness.status(queued.id) is TaskStatus.CANCELLED
    assert harness.services.runs(queued.id) == 0
    harness.services.release.set()
    await _settle(harness.supervisor)


@pytest.mark.asyncio
async def test_cancelling_a_running_task_interrupts_its_pass_and_records_cancelled() -> None:
    harness = Harness()
    await harness.ready()
    harness.services.release.clear()
    task = await harness.submit("k1")
    await harness.supervisor._dispatch()
    await harness.services.started_event(task.id).wait()

    await harness.repository.append_command(task.partition(), task.id, CommandKind.CANCEL, None, "cancel-k1")
    harness.supervisor.request_cancel(local_attempt_id(task.id))
    await _settle(harness.supervisor)

    assert await harness.status(task.id) is TaskStatus.CANCELLED
    assert ("cancel", task.id) in harness.services.calls
    assert (await harness.ledger.snapshot()).entries == ()


@pytest.mark.asyncio
async def test_a_cancellation_received_by_another_replica_reaches_the_owner_through_renewal() -> None:
    harness = Harness()
    await harness.ready()
    harness.services.release.clear()
    task = await harness.submit("k2")
    await harness.supervisor._dispatch()
    await harness.services.started_event(task.id).wait()

    await harness.repository.append_command(task.partition(), task.id, CommandKind.CANCEL, None, "cancel-k2")
    await harness.supervisor._renew()
    await _settle(harness.supervisor)

    assert await harness.status(task.id) is TaskStatus.CANCELLED


@pytest.mark.asyncio
async def test_a_task_that_waited_too_long_fails_with_queue_timeout() -> None:
    harness = Harness(limits=SupervisorLimits(max_active_per_replica=1, deployment_limit=1))
    await harness.ready()
    harness.services.release.clear()
    blocking = await harness.submit("t1")
    waiting = await harness.submit("t2")
    await harness.supervisor._dispatch()
    await harness.services.started_event(blocking.id).wait()

    harness.clock.advance(timedelta(minutes=16))
    await harness.supervisor._dispatch()

    assert await harness.status(waiting.id) is TaskStatus.FAILED
    assert ("fail", waiting.id, QUEUE_TIMEOUT_FAILURE) in harness.services.calls
    harness.services.release.set()
    await _settle(harness.supervisor)


@pytest.mark.asyncio
async def test_a_task_past_its_wall_clock_budget_is_stopped_and_failed() -> None:
    harness = Harness()
    await harness.ready()
    harness.services.release.clear()
    task = await harness.submit("b1", created_at=harness.clock.now - timedelta(minutes=59, seconds=45))
    await harness.supervisor._dispatch()
    await harness.services.started_event(task.id).wait()

    # Still within its lease, but past the budget counted from when the task was created.
    harness.clock.advance(timedelta(seconds=30))
    await harness.supervisor._renew()
    await _settle(harness.supervisor)

    assert await harness.status(task.id) is TaskStatus.FAILED
    assert ("fail", task.id, BUDGET_FAILURE) in harness.services.calls


@pytest.mark.asyncio
async def test_a_task_whose_budget_was_spent_before_it_started_fails_without_running() -> None:
    harness = Harness()
    await harness.ready()
    task = await harness.submit("b2", created_at=harness.clock.now - timedelta(minutes=61))

    await harness.supervisor._dispatch()
    await _settle(harness.supervisor)

    assert await harness.status(task.id) is TaskStatus.FAILED
    assert harness.services.runs(task.id) == 0


@pytest.mark.asyncio
async def test_an_orphaned_task_is_recovered_after_its_claim_expires_and_its_sandbox_is_reset() -> None:
    harness = Harness()
    crashed = harness.make_supervisor("replica-crashed")
    await harness.ready(crashed)
    harness.services.release.clear()
    task = await harness.admit(crashed, await harness.create("r1"))
    await crashed._dispatch()
    await harness.services.started_event(task.id).wait()
    # The replica dies: its job stops, and its claim is neither released nor renewed.
    for running in crashed._running.values():
        assert running.job is not None
        running.lost = True
        running.job.cancel()
    await asyncio.sleep(0)
    await harness.ready()

    await harness.supervisor._scan()
    assert harness.supervisor.queued_task_ids() == ()

    harness.clock.advance(timedelta(seconds=41))
    harness.services.release.set()
    await harness.supervisor._scan()
    await harness.supervisor._dispatch()
    await _settle(harness.supervisor)

    assert await harness.status(task.id) is TaskStatus.COMPLETED
    assert ("reset", task.id) in harness.services.calls


@pytest.mark.asyncio
async def test_a_recovered_task_whose_answer_was_already_published_is_only_checkpointed() -> None:
    harness = Harness()
    await harness.ready()
    task = await harness.create("p1")
    await harness.repository.replace_active_attempt(task.id, local_attempt_id(task.id), expected_attempt_id=None)
    analyzing = await harness.repository.transition_task(task.id, TaskStatus.ANALYZING, 0)
    await harness.repository.set_final_message(task.id, "msg_final_published")
    assert analyzing.status is TaskStatus.ANALYZING

    await harness.supervisor._scan()
    await harness.supervisor._dispatch()
    await _settle(harness.supervisor)

    completed = await harness.repository.resolve_task(task.id)
    assert completed is not None
    assert completed.status is TaskStatus.COMPLETED
    assert completed.final_message_id == "msg_final_published"
    assert harness.services.runs(task.id) == 0


@pytest.mark.asyncio
async def test_legacy_hosted_tasks_are_never_recovered_by_the_local_runtime() -> None:
    harness = Harness()
    await harness.ready()
    task = await harness.create("h1")
    await harness.repository.replace_active_attempt(task.id, "resp_legacy000001", expected_attempt_id=None)

    await harness.supervisor._scan()

    assert harness.supervisor.queued_task_ids() == ()


@pytest.mark.asyncio
async def test_losing_the_claim_stops_the_job_without_any_further_write() -> None:
    harness = Harness()
    await harness.ready()
    harness.services.release.clear()
    task = await harness.submit("f1")
    await harness.supervisor._dispatch()
    await harness.services.started_event(task.id).wait()

    # The claim expires while this replica is stalled and another replica takes the task.
    harness.clock.advance(timedelta(seconds=41))
    outcome, claim = await harness.ledger.claim(
        task.id, task.session_id, "replica-b", ttl=timedelta(seconds=40), limit=5
    )
    assert outcome == "claimed" and claim is not None
    writes_before = len([call for call in harness.services.calls if call[0] in {"checkpoint", "complete"}])

    await harness.supervisor._renew()
    await _settle(harness.supervisor)

    writes_after = len([call for call in harness.services.calls if call[0] in {"checkpoint", "complete"}])
    assert writes_after == writes_before
    assert await harness.ledger.is_held(claim)
    assert await harness.status(task.id) is TaskStatus.ANALYZING


@pytest.mark.asyncio
async def test_shutdown_leaves_a_running_task_recoverable_and_frees_its_claim() -> None:
    harness = Harness()
    await harness.ready()
    harness.services.release.clear()
    task = await harness.submit("s1")
    await harness.supervisor._dispatch()
    await harness.services.started_event(task.id).wait()

    await harness.supervisor.stop()

    assert await harness.status(task.id) is TaskStatus.ANALYZING
    assert (await harness.ledger.snapshot()).entries == ()
    successor = harness.make_supervisor("replica-next")
    await harness.ready(successor)
    harness.services.release.set()
    await successor._scan()
    await successor._dispatch()
    await _settle(successor)
    assert await harness.status(task.id) is TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_repeated_infrastructure_errors_end_in_a_bounded_failure() -> None:
    harness = Harness(limits=SupervisorLimits(max_infrastructure_attempts=2))
    await harness.ready()
    task = await harness.submit("e1")

    class Broken(RuntimeError):
        pass

    original = harness.services.checkpoint

    async def broken_checkpoint(task_id: str, status: TaskStatus, expected: int) -> TaskRecord:
        if status is TaskStatus.ANALYZING:
            raise Broken("cosmos unavailable")
        return await original(task_id, status, expected)

    harness.services.checkpoint = broken_checkpoint  # type: ignore[method-assign]
    await harness.supervisor._dispatch()
    await _settle(harness.supervisor)
    assert await harness.status(task.id) is TaskStatus.PLANNING

    await harness.supervisor._scan()
    await harness.supervisor._dispatch()
    await _settle(harness.supervisor)

    assert await harness.status(task.id) is TaskStatus.FAILED
    assert ("fail", task.id, RUNTIME_ERROR_FAILURE) in harness.services.calls


def test_limits_reject_a_lease_that_cannot_survive_missed_renewals() -> None:
    with pytest.raises(ValueError, match="missed renewals"):
        SupervisorLimits(lease_ttl=timedelta(seconds=20), renew_interval=10.0)
    with pytest.raises(ValueError, match="inconsistent"):
        SupervisorLimits(max_active_per_replica=3, deployment_limit=2)


@pytest.mark.asyncio
async def test_a_cancellation_whose_sandbox_will_not_stop_is_recorded_as_failed_cancellation() -> None:
    harness = Harness(limits=SupervisorLimits(max_active_per_replica=1, deployment_limit=1))
    await harness.ready()
    harness.services.release.clear()
    running = await harness.submit("z1")
    queued = await harness.submit("z2")
    await harness.supervisor._dispatch()
    await harness.services.started_event(running.id).wait()

    async def stuck_cancel(task_id: str) -> None:
        raise RuntimeError("sandbox delete refused")

    harness.services.cancel = stuck_cancel  # type: ignore[method-assign]
    await harness.repository.append_command(queued.partition(), queued.id, CommandKind.CANCEL, None, "cancel-z2")
    harness.supervisor.request_cancel(local_attempt_id(queued.id))
    await harness.supervisor._dispatch()

    assert await harness.status(queued.id) is TaskStatus.FAILED_CANCELLATION
    harness.services.release.set()
    await _settle(harness.supervisor)


@pytest.mark.asyncio
async def test_a_failure_is_recorded_even_when_sandbox_cleanup_keeps_failing() -> None:
    harness = Harness(limits=SupervisorLimits(max_infrastructure_attempts=2))
    await harness.ready()
    task = await harness.create("y1")
    await harness.repository.replace_active_attempt(task.id, local_attempt_id(task.id), expected_attempt_id=None)

    async def broken_reset(task_id: str) -> None:
        raise RuntimeError("sandbox delete refused")

    async def broken_fail(task_id: str, failure_code: str) -> None:
        raise RuntimeError("sandbox delete refused")

    harness.services.reset_execution = broken_reset  # type: ignore[method-assign]
    harness.services.fail = broken_fail  # type: ignore[method-assign]
    for _ in range(2):
        await harness.supervisor._scan()
        await harness.supervisor._dispatch()
        await _settle(harness.supervisor)

    assert await harness.status(task.id) is TaskStatus.FAILED
    assert harness.services.runs(task.id) == 0


@pytest.mark.asyncio
async def test_a_stalled_renewal_stops_the_job_before_its_claim_can_expire() -> None:
    harness = Harness(limits=SupervisorLimits(lease_ttl=timedelta(seconds=3), renew_interval=1.0))
    await harness.ready()
    harness.services.release.clear()
    task = await harness.submit("w1")
    await harness.supervisor._dispatch()
    await harness.services.started_event(task.id).wait()

    async def stalled_renew(claims: object, *, ttl: timedelta) -> set[str]:
        del claims, ttl
        await asyncio.Event().wait()
        return set()

    harness.ledger.renew = stalled_renew  # type: ignore[method-assign]
    # One renewal interval before expiry, the job stops rather than risk writing after another owner took over.
    harness.clock.advance(timedelta(seconds=1.1))
    await harness.supervisor._renew()
    await _settle(harness.supervisor)

    assert await harness.status(task.id) is TaskStatus.ANALYZING
    assert ("complete", task.id) not in harness.services.calls


class RecordedEvents:
    def __init__(self) -> None:
        self.drafts: list[object] = []

    async def append(self, draft: object) -> object:
        self.drafts.append(draft)
        return draft


@pytest.mark.asyncio
async def test_each_execution_streams_under_its_own_segment_ids_and_recovery_does_not_stream() -> None:
    from eda_api.analysis.streaming import TaskStreams

    harness = Harness()
    events = RecordedEvents()
    streams = TaskStreams(events, batch_chars=1)
    harness.supervisor = AnalysisSupervisor(
        services=harness.services,
        tasks=harness.repository,
        ledger=harness.ledger,
        streams=streams,
        replica_id="replica-a",
        sleep=_never,
    )
    await harness.ready()
    harness.services.release.clear()
    task = await harness.submit("v1")
    await harness.supervisor._dispatch()
    await harness.services.started_event(task.id).wait()
    sink = streams.sink_for(task.id)
    assert sink is not None
    [running] = harness.supervisor._running.values()
    assert sink.segment_id == f"{local_attempt_id(task.id)}.{running.claim.fence[:8]}.0"
    harness.services.release.set()
    await _settle(harness.supervisor)

    recovered = await harness.create("v2")
    await harness.repository.replace_active_attempt(
        recovered.id, local_attempt_id(recovered.id), expected_attempt_id=None
    )
    harness.services.release.clear()
    await harness.supervisor._scan()
    await harness.supervisor._dispatch()
    await harness.services.started_event(recovered.id).wait()

    assert streams.sink_for(recovered.id) is None
    harness.services.release.set()
    await _settle(harness.supervisor)
