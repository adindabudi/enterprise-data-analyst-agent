"""The analyst runtime's scheduler inside the API replica.

Every analysis task runs here, as a plain asyncio job driving the worker's task
lifecycle; there is no hosted agent and no second engine. The durable truth is
the task record and the execution ledger in Cosmos, never this process's memory:

* A task is persisted with its `run_` attempt before it is queued, so a task this
  replica accepted but never ran is found again by any replica's recovery scan.
* Executing needs a ledger claim, which enforces the deployment-wide limit, one
  running task per conversation, and one owner per task. A claim is renewed while
  the task runs; losing it stops the job before it writes anything else.
* A task found without a live claim is recovered: whatever a previous owner left
  in the sandbox is deleted first, and an answer that was already published is
  checkpointed rather than produced twice.
* Cancellation, queue age and the wall-clock budget are enforced here, so a task
  cannot wait or run without bound.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import secrets
import socket
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal, Protocol

from eda_contracts.tasks import TaskStatus
from eda_runtime_state.ledger import ClaimOutcome, ExecutionClaim, ExecutionLedger
from eda_runtime_state.models import TERMINAL, TaskRecord
from eda_worker.lifecycle import AnalysisServices, OwnershipLost, run_task_lifecycle

from .admission import AdmissionRejected
from .streaming import TaskStreams

logger = logging.getLogger(__name__)

LOCAL_ATTEMPT_PREFIX = "run_"
BUDGET_FAILURE = "analysis_budget_exhausted"
QUEUE_TIMEOUT_FAILURE = "queue_timeout"
RUNTIME_ERROR_FAILURE = "analysis_runtime_error"
# Settling a task that never ran is a few state writes, so it does not count against execution capacity.
SETTLEMENT_LIMIT = 1_000
SCAN_LIMIT = 200

SupervisorState = Literal["starting", "ready", "blocked", "stopped"]
StopReason = Literal["cancel", "budget", "shutdown", "ownership"]


def local_attempt_id(task_id: str) -> str:
    """Deterministic, so a retried dispatch of the same task persists the same attempt."""
    return f"{LOCAL_ATTEMPT_PREFIX}{hashlib.sha256(f'{task_id}|analysis-runtime'.encode()).hexdigest()[:32]}"


def is_local_attempt(attempt_id: str | None) -> bool:
    return attempt_id is not None and attempt_id.startswith(LOCAL_ATTEMPT_PREFIX)


def owner_key(task: TaskRecord) -> str:
    return f"{task.tenant_id}:{task.owner_object_id}"


class SupervisedServices(AnalysisServices, Protocol):
    async def warmup(self) -> None: ...

    async def reset_execution(self, task_id: str) -> None: ...


class ActiveTaskIndex(Protocol):
    async def active_task_ids(self, attempt_prefix: str, *, limit: int = 200) -> list[str]: ...


class ExecutionLedgerPort(Protocol):
    def now(self) -> datetime: ...

    async def claim(
        self,
        task_id: str,
        session_id: str,
        owner: str,
        *,
        ttl: timedelta,
        limit: int,
        exclusive_conversation: bool = True,
    ) -> tuple[ClaimOutcome, ExecutionClaim | None]: ...

    async def renew(self, claims: Mapping[str, str], *, ttl: timedelta) -> set[str]: ...

    async def release(self, claim: ExecutionClaim) -> None: ...

    async def is_held(self, claim: ExecutionClaim) -> bool: ...

    async def snapshot(self) -> ExecutionLedger: ...


@dataclass(frozen=True)
class SupervisorLimits:
    max_active_per_replica: int = 2
    deployment_limit: int = 5
    queue_depth: int = 50
    per_owner_limit: int = 10
    max_queue_age: timedelta = timedelta(minutes=15)
    task_budget: timedelta = timedelta(minutes=60)
    lease_ttl: timedelta = timedelta(seconds=40)
    renew_interval: float = 10.0
    scan_interval: float = 10.0
    warmup_retry_interval: float = 30.0
    shutdown_grace: float = 20.0
    max_infrastructure_attempts: int = 3

    def __post_init__(self) -> None:
        if self.max_active_per_replica < 1 or self.deployment_limit < self.max_active_per_replica:
            raise ValueError("analysis concurrency limits are inconsistent")
        if self.queue_depth < 1 or self.per_owner_limit < 1:
            raise ValueError("analysis queue limits must be positive")
        if self.renew_interval * 3 > self.lease_ttl.total_seconds():
            raise ValueError("a lease must survive at least two missed renewals")


@dataclass
class Reservation:
    owner_key: str
    active: bool = True


@dataclass
class _Queued:
    task_id: str
    session_id: str
    owner_key: str
    enqueued_at: datetime
    recovered: bool
    cancel_requested: bool = False


@dataclass
class _Running:
    entry: _Queued
    claim: ExecutionClaim
    renewed_at: datetime
    job: asyncio.Task[None] | None = None
    created_at: datetime | None = None
    stop_reason: StopReason | None = None
    lost: bool = False


class AnalysisSupervisor:
    def __init__(
        self,
        *,
        services: SupervisedServices,
        tasks: ActiveTaskIndex,
        ledger: ExecutionLedgerPort,
        streams: TaskStreams | None = None,
        replica_id: str | None = None,
        limits: SupervisorLimits | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._services = services
        self._tasks = tasks
        self._ledger = ledger
        self._streams = streams
        self._limits = limits or SupervisorLimits()
        self._sleep = sleep
        self._replica_id = replica_id or f"{socket.gethostname()[:80]}-{secrets.token_hex(6)}"
        self._state: SupervisorState = "starting"
        self._queue: list[_Queued] = []
        self._reserved: dict[str, int] = {}
        self._running: dict[str, _Running] = {}
        self._attempts: dict[str, str] = {}
        self._infrastructure_failures: dict[str, int] = {}
        self._last_owner: str | None = None
        self._wakeup = asyncio.Event()
        self._loops: list[asyncio.Task[None]] = []

    @property
    def replica_id(self) -> str:
        return self._replica_id

    @property
    def state(self) -> SupervisorState:
        return self._state

    def readiness(self) -> Literal["ready", "starting", "blocked"]:
        if self._state == "ready":
            return "ready"
        return "starting" if self._state == "starting" else "blocked"

    def queued_task_ids(self) -> tuple[str, ...]:
        return tuple(entry.task_id for entry in self._queue)

    def running_task_ids(self) -> tuple[str, ...]:
        return tuple(self._running)

    async def start(self) -> None:
        if self._loops:
            return
        self._loops = [
            asyncio.create_task(self._warmup_loop(), name="analysis-warmup"),
            asyncio.create_task(self._dispatch_loop(), name="analysis-dispatch"),
            asyncio.create_task(self._renew_loop(), name="analysis-renew"),
            asyncio.create_task(self._scan_loop(), name="analysis-recovery"),
        ]

    async def stop(self) -> None:
        """Stop taking work and interrupt running tasks without settling them; their next owner recovers them."""
        self._state = "stopped"
        for loop in self._loops:
            loop.cancel()
        await asyncio.gather(*self._loops, return_exceptions=True)
        self._loops = []
        jobs: list[asyncio.Task[None]] = []
        for running in list(self._running.values()):
            self._stop(running, "shutdown")
            if running.job is not None:
                jobs.append(running.job)
        if jobs:
            await asyncio.wait(jobs, timeout=self._limits.shutdown_grace)

    # Admission -----------------------------------------------------------------------------------

    def reserve(self, owner: str) -> Reservation:
        # Only a runtime that finished warming up accepts work, so nothing persisted can wait on one that never will.
        if self._state != "ready":
            raise AdmissionRejected("runtime_unavailable")
        if len(self._queue) + sum(self._reserved.values()) >= self._limits.queue_depth:
            raise AdmissionRejected("queue_full")
        if self._owner_load(owner) >= self._limits.per_owner_limit:
            raise AdmissionRejected("owner_limit")
        self._reserved[owner] = self._reserved.get(owner, 0) + 1
        return Reservation(owner)

    def release(self, reservation: Reservation) -> None:
        if not reservation.active:
            return
        reservation.active = False
        remaining = self._reserved.get(reservation.owner_key, 0) - 1
        if remaining > 0:
            self._reserved[reservation.owner_key] = remaining
        else:
            self._reserved.pop(reservation.owner_key, None)

    def enqueue(self, reservation: Reservation, *, task_id: str, session_id: str, attempt_id: str) -> None:
        """Queue a task whose attempt is already persisted."""
        self.release(reservation)
        self._attempts[attempt_id] = task_id
        if task_id in self._running or self._is_queued(task_id):
            return
        self._queue.append(
            _Queued(
                task_id=task_id,
                session_id=session_id,
                owner_key=reservation.owner_key,
                enqueued_at=self._ledger.now(),
                recovered=False,
            )
        )
        self._wake()

    def request_cancel(self, attempt_id: str) -> None:
        """Stop a task this replica is running or holding; a task elsewhere is stopped by its owner."""
        task_id = self._attempts.get(attempt_id)
        if task_id is None:
            return
        running = self._running.get(task_id)
        if running is not None:
            self._stop(running, "cancel")
            return
        for entry in self._queue:
            if entry.task_id == task_id:
                entry.cancel_requested = True
                self._wake()
                return

    # Loops ---------------------------------------------------------------------------------------

    async def _warmup_loop(self) -> None:
        while self._state in {"starting", "blocked"}:
            try:
                await self._services.warmup()
            except Exception:
                logger.exception("analysis runtime is unavailable; retrying")
                self._state = "blocked"
                await self._sleep(self._limits.warmup_retry_interval)
                continue
            self._state = "ready"
            self._wake()
            return

    async def _dispatch_loop(self) -> None:
        while True:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wakeup.wait(), timeout=self._limits.scan_interval)
            self._wakeup.clear()
            try:
                await self._dispatch()
            except Exception:
                logger.exception("analysis dispatch failed")

    async def _renew_loop(self) -> None:
        while True:
            await self._sleep(self._limits.renew_interval)
            try:
                await self._renew()
            except Exception:
                logger.exception("analysis lease renewal failed")

    async def _scan_loop(self) -> None:
        while True:
            await self._sleep(self._limits.scan_interval)
            if self._state != "ready":
                continue
            try:
                await self._scan()
            except Exception:
                logger.exception("analysis recovery scan failed")

    # Dispatch ------------------------------------------------------------------------------------

    async def _dispatch(self) -> None:
        if self._state != "ready" or not self._queue:
            return
        await self._settle_queued()
        if not self._queue or len(self._running) >= self._limits.max_active_per_replica:
            return
        snapshot = await self._ledger.snapshot()
        live = snapshot.live(self._ledger.now())
        busy_sessions = {entry.session_id for entry in live}
        held_elsewhere = {entry.task_id for entry in live if entry.owner != self._replica_id}
        live_count = len(live)
        for entry in self._fair_order():
            if len(self._running) >= self._limits.max_active_per_replica:
                return
            if live_count >= self._limits.deployment_limit:
                return
            if entry.task_id in held_elsewhere:
                self._drop(entry)
                continue
            if entry.session_id in busy_sessions:
                continue
            outcome, claim = await self._ledger.claim(
                entry.task_id,
                entry.session_id,
                self._replica_id,
                ttl=self._limits.lease_ttl,
                limit=self._limits.deployment_limit,
            )
            if outcome == "claimed" and claim is not None:
                self._drop(entry, forget=False)
                self._launch(entry, claim)
                busy_sessions.add(entry.session_id)
                live_count += 1
                self._last_owner = entry.owner_key
            elif outcome == "capacity_full":
                return
            elif outcome == "owned_elsewhere":
                self._drop(entry)
            else:
                busy_sessions.add(entry.session_id)

    def _fair_order(self) -> list[_Queued]:
        """Round-robin across owners, oldest first within each owner, starting after the last owner served."""
        by_owner: OrderedDict[str, list[_Queued]] = OrderedDict()
        for entry in self._queue:
            by_owner.setdefault(entry.owner_key, []).append(entry)
        owners = list(by_owner)
        if self._last_owner in by_owner:
            index = owners.index(self._last_owner) + 1
            owners = owners[index:] + owners[:index]
        ordered: list[_Queued] = []
        depth = max((len(entries) for entries in by_owner.values()), default=0)
        for level in range(depth):
            for owner in owners:
                entries = by_owner[owner]
                if level < len(entries):
                    ordered.append(entries[level])
        return ordered

    async def _settle_queued(self) -> None:
        now = self._ledger.now()
        for entry in list(self._queue):
            expired = now - entry.enqueued_at >= self._limits.max_queue_age
            if not entry.cancel_requested and not expired:
                continue
            outcome, claim = await self._ledger.claim(
                entry.task_id,
                entry.session_id,
                self._replica_id,
                ttl=self._limits.lease_ttl,
                limit=SETTLEMENT_LIMIT,
                exclusive_conversation=False,
            )
            if outcome != "claimed" or claim is None:
                # Whoever holds it now settles it.
                self._drop(entry)
                continue
            try:
                if entry.cancel_requested:
                    await self._settle_cancelled(entry.task_id)
                else:
                    await self._settle_failed(entry.task_id, QUEUE_TIMEOUT_FAILURE)
            except Exception:
                logger.exception("queued analysis task %s could not be settled", entry.task_id)
            finally:
                self._drop(entry)
                await self._release(claim)

    def _launch(self, entry: _Queued, claim: ExecutionClaim) -> None:
        running = _Running(entry=entry, claim=claim, renewed_at=self._ledger.now())
        self._running[entry.task_id] = running
        running.job = asyncio.create_task(self._execute(running), name=f"analysis:{entry.task_id}")

    # Execution -----------------------------------------------------------------------------------

    async def _execute(self, running: _Running) -> None:
        task_id = running.entry.task_id
        release = True
        try:
            await self._run(running)
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is not None:
                current.uncancel()
            reason = running.stop_reason
            if running.lost or reason == "ownership":
                release = False
            elif reason == "cancel":
                await self._quietly(self._settle_cancelled(task_id))
            elif reason == "budget":
                await self._quietly(self._settle_failed(task_id, BUDGET_FAILURE))
            # "shutdown" leaves the task as it is: the job has stopped, so the next owner can take over.
        except OwnershipLost:
            release = False
            logger.warning("analysis task %s lost its execution claim; stopped without further writes", task_id)
        except Exception:
            logger.exception("analysis task %s stopped on an infrastructure error", task_id)
            attempts = self._infrastructure_failures.get(task_id, 0) + 1
            self._infrastructure_failures[task_id] = attempts
            if attempts >= self._limits.max_infrastructure_attempts and not running.lost:
                # The count is cleared only once the failure is recorded, so a failing settlement retries it.
                if await self._quietly(self._settle_failed(task_id, RUNTIME_ERROR_FAILURE)):
                    self._infrastructure_failures.pop(task_id, None)
        finally:
            if self._streams is not None:
                await self._streams.close(task_id)
            self._running.pop(task_id, None)
            if release:
                await self._release(running.claim)
            self._forget(task_id)
            self._wake()

    async def _run(self, running: _Running) -> None:
        task_id = running.entry.task_id
        task = await self._services.task(task_id)
        if task is None or task.status in TERMINAL:
            return
        attempt_id = task.active_attempt_id
        if attempt_id is None or not is_local_attempt(attempt_id):
            return
        running.created_at = task.created_at
        self._attempts[attempt_id] = task_id

        async def ensure_owner() -> None:
            if running.lost or not await self._ledger.is_held(running.claim):
                running.lost = True
                raise OwnershipLost(task_id)

        async def on_pass(pass_index: int) -> None:
            if self._streams is not None:
                await self._streams.begin_pass(task_id, pass_index)

        await ensure_owner()
        if self._budget_spent(task.created_at):
            await self._settle_failed(task_id, BUDGET_FAILURE)
            return
        if running.entry.recovered:
            # Whatever a previous owner started in the sandbox is deleted before anything reruns.
            await self._services.reset_execution(task_id)
            if task.final_message_id is not None and task.status is TaskStatus.ANALYZING:
                # The answer was published before the interruption; only its checkpoint is missing.
                await ensure_owner()
                await self._services.checkpoint(task_id, TaskStatus.COMPLETED, task.checkpoint_sequence)
                return
        elif self._streams is not None:
            # A recovered run is not streamed: a viewer may still hold the interrupted run's text, and the
            # final message replaces it. Each execution streams under its own generation of segment IDs.
            self._streams.open(
                task_id, session_id=task.session_id, attempt_id=f"{attempt_id}.{running.claim.fence[:8]}"
            )

        result = await run_task_lifecycle(self._services, task_id, ensure_owner=ensure_owner, on_pass=on_pass)
        self._infrastructure_failures.pop(task_id, None)
        logger.info(
            "analysis task %s finished with %s",
            task_id,
            result.status.value,
            extra={"task_id": task_id, "failure_code": result.failure_code},
        )

    async def _settle_cancelled(self, task_id: str) -> None:
        task = await self._services.task(task_id)
        if task is None or task.status in TERMINAL:
            return
        current = task
        if current.status is not TaskStatus.CANCELLING:
            current = await self._services.checkpoint(task_id, TaskStatus.CANCELLING, task.checkpoint_sequence)
        try:
            await self._services.cancel(task_id)
        except Exception:
            # The cancellation is recorded truthfully rather than retried forever against a sandbox that will not stop.
            logger.exception("sandbox work for cancelled task %s could not be stopped", task_id)
            await self._services.checkpoint(task_id, TaskStatus.FAILED_CANCELLATION, current.checkpoint_sequence)
            return
        await self._services.checkpoint(task_id, TaskStatus.CANCELLED, current.checkpoint_sequence)

    async def _settle_failed(self, task_id: str, failure_code: str) -> None:
        task = await self._services.task(task_id)
        if task is None or task.status in TERMINAL:
            return
        if task.cancellation_requested or task.status is TaskStatus.CANCELLING:
            await self._settle_cancelled(task_id)
            return
        try:
            await self._services.fail(task_id, failure_code)
        except Exception:
            # Stopping the sandbox is cleanup; the failure is recorded even when cleanup cannot finish.
            logger.exception("sandbox work for failed task %s could not be stopped", task_id)
        await self._services.checkpoint(task_id, TaskStatus.FAILED, task.checkpoint_sequence)

    # Leases, cancellation and budgets ----------------------------------------------------------

    async def _renew(self) -> None:
        active = {task_id: running for task_id, running in self._running.items() if not running.lost}
        if not active:
            return
        try:
            # Bounded, so a stalled store cannot keep a job running past the point its claim may have expired.
            held = await asyncio.wait_for(
                self._ledger.renew(
                    {task_id: running.claim.fence for task_id, running in active.items()},
                    ttl=self._limits.lease_ttl,
                ),
                timeout=self._limits.renew_interval / 2,
            )
        except Exception:
            logger.exception("analysis leases could not be renewed")
            # Stop while a claim still has at least one renewal interval left, so no other owner
            # can have taken the task by the time this job stops writing.
            margin = self._limits.lease_ttl - timedelta(seconds=2 * self._limits.renew_interval)
            now = self._ledger.now()
            for running in active.values():
                if now - running.renewed_at >= margin:
                    self._stop(running, "ownership")
            return
        # The renewal wrote an expiry computed before the call returned; this is a conservative start for it.
        renewed_at = self._ledger.now() - timedelta(seconds=self._limits.renew_interval / 2)
        for task_id, running in active.items():
            if task_id in held:
                running.renewed_at = max(running.renewed_at, renewed_at)
            else:
                self._stop(running, "ownership")
        await self._watch(active)

    async def _watch(self, active: Mapping[str, _Running]) -> None:
        for task_id, running in active.items():
            if running.lost or running.stop_reason is not None:
                continue
            if running.created_at is not None and self._budget_spent(running.created_at):
                self._stop(running, "budget")
                continue
            try:
                task = await self._services.task(task_id)
            except Exception:
                logger.warning("analysis task %s could not be checked for cancellation", task_id)
                continue
            # Covers a cancellation received by another replica.
            if task is not None and task.cancellation_requested:
                self._stop(running, "cancel")

    def _stop(self, running: _Running, reason: StopReason) -> None:
        if reason == "ownership":
            running.lost = True
            running.stop_reason = "ownership"
        elif running.stop_reason is None:
            running.stop_reason = reason
        else:
            return
        if running.job is not None and not running.job.done():
            running.job.cancel()

    # Recovery ------------------------------------------------------------------------------------

    async def _scan(self) -> None:
        task_ids = await self._tasks.active_task_ids(LOCAL_ATTEMPT_PREFIX, limit=SCAN_LIMIT)
        candidates = [task_id for task_id in task_ids if not self._is_known(task_id)]
        if not candidates:
            return
        snapshot = await self._ledger.snapshot()
        live = {entry.task_id for entry in snapshot.live(self._ledger.now())}
        found = False
        for task_id in candidates:
            if task_id in live:
                continue
            task = await self._services.task(task_id)
            if task is None or task.status in TERMINAL or not is_local_attempt(task.active_attempt_id):
                continue
            if self._is_known(task_id):
                continue
            self._attempts[str(task.active_attempt_id)] = task_id
            self._queue.append(
                _Queued(
                    task_id=task_id,
                    session_id=task.session_id,
                    owner_key=owner_key(task),
                    enqueued_at=self._ledger.now(),
                    recovered=True,
                    cancel_requested=task.cancellation_requested,
                )
            )
            found = True
        if found:
            self._wake()

    # Helpers -------------------------------------------------------------------------------------

    def _owner_load(self, owner: str) -> int:
        queued = sum(1 for entry in self._queue if entry.owner_key == owner)
        running = sum(1 for running in self._running.values() if running.entry.owner_key == owner)
        return queued + running + self._reserved.get(owner, 0)

    def _is_queued(self, task_id: str) -> bool:
        return any(entry.task_id == task_id for entry in self._queue)

    def _is_known(self, task_id: str) -> bool:
        return task_id in self._running or self._is_queued(task_id)

    def _drop(self, entry: _Queued, *, forget: bool = True) -> None:
        with contextlib.suppress(ValueError):
            self._queue.remove(entry)
        if forget:
            self._forget(entry.task_id)

    def _forget(self, task_id: str) -> None:
        if self._is_known(task_id):
            return
        for attempt_id, known_task_id in list(self._attempts.items()):
            if known_task_id == task_id:
                del self._attempts[attempt_id]

    def _budget_spent(self, created_at: datetime) -> bool:
        return self._ledger.now() - created_at >= self._limits.task_budget

    async def _release(self, claim: ExecutionClaim) -> None:
        try:
            await self._ledger.release(claim)
        except Exception:
            # An unreleased claim expires by itself; the task is not affected.
            logger.exception("analysis claim for task %s could not be released", claim.task_id)

    @staticmethod
    async def _quietly(operation: Coroutine[Any, Any, None]) -> bool:
        # Awaited, never detached: a settlement must not outlive the job that owns the claim.
        try:
            await operation
        except Exception:
            logger.exception("analysis task settlement failed")
            return False
        return True

    def _wake(self) -> None:
        self._wakeup.set()
