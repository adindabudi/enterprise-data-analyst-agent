"""Task dispatch for the single analyst runtime.

`TaskService` dispatches through a small client protocol that used to reach the
hosted agent. `LocalAnalysisClient` implements it in-process: it persists the
task's `run_` attempt and only then hands the task to the supervisor, so an
accepted task is durable before anything runs it. `RoutingTaskClient` keeps
tasks that the hosted agent already owns on that path until they finish; no new
task is ever sent there.
"""

from __future__ import annotations

from typing import Protocol

from eda_runtime_state.models import TaskRecord
from eda_runtime_state.tasks import RuntimeStateConflict

from eda_api.hosted_responses import HostedResponseAttempt, HostedResponseStatus
from eda_api.task_service import HostedTaskClient

from .supervisor import AnalysisSupervisor, is_local_attempt, local_attempt_id, owner_key


class TaskAttemptStore(Protocol):
    async def resolve_task(self, task_id: str) -> TaskRecord | None: ...

    async def replace_active_attempt(
        self,
        task_id: str,
        attempt_id: str,
        *,
        expected_attempt_id: str | None,
    ) -> TaskRecord: ...


class LocalAnalysisClient:
    def __init__(self, supervisor: AnalysisSupervisor, repository: TaskAttemptStore) -> None:
        self._supervisor = supervisor
        self._repository = repository

    async def start(
        self,
        task_id: str,
        *,
        user_identity: str,
        previous_response_id: str | None = None,
    ) -> HostedResponseAttempt:
        del user_identity, previous_response_id
        task = await self._repository.resolve_task(task_id)
        if task is None:
            raise RuntimeStateConflict("task is unavailable")
        reservation = self._supervisor.reserve(owner_key(task))
        attempt_id = local_attempt_id(task_id)
        try:
            await self._repository.replace_active_attempt(task_id, attempt_id, expected_attempt_id=None)
        except BaseException:
            self._supervisor.release(reservation)
            raise
        self._supervisor.enqueue(reservation, task_id=task.id, session_id=task.session_id, attempt_id=attempt_id)
        return HostedResponseAttempt(id=attempt_id, status=HostedResponseStatus.QUEUED)

    async def get(self, response_id: str, *, user_identity: str) -> HostedResponseAttempt:
        # The supervisor owns recovery of its own tasks; reconciliation must not settle them from outside.
        del user_identity
        return HostedResponseAttempt(id=response_id, status=HostedResponseStatus.IN_PROGRESS)

    async def cancel(self, response_id: str, *, user_identity: str) -> HostedResponseAttempt:
        del user_identity
        self._supervisor.request_cancel(response_id)
        return HostedResponseAttempt(id=response_id, status=HostedResponseStatus.IN_PROGRESS)

    async def close(self) -> None:
        return None


class RoutingTaskClient:
    """New tasks run in-process; tasks the hosted agent already owns stay with it until they finish."""

    def __init__(self, local: LocalAnalysisClient, legacy: HostedTaskClient | None = None) -> None:
        self._local = local
        self._legacy = legacy

    async def start(
        self,
        task_id: str,
        *,
        user_identity: str,
        previous_response_id: str | None = None,
    ) -> HostedResponseAttempt:
        return await self._local.start(task_id, user_identity=user_identity, previous_response_id=previous_response_id)

    async def get(self, response_id: str, *, user_identity: str) -> HostedResponseAttempt:
        if is_local_attempt(response_id) or self._legacy is None:
            return await self._local.get(response_id, user_identity=user_identity)
        return await self._legacy.get(response_id, user_identity=user_identity)

    async def cancel(self, response_id: str, *, user_identity: str) -> HostedResponseAttempt:
        if is_local_attempt(response_id) or self._legacy is None:
            return await self._local.cancel(response_id, user_identity=user_identity)
        return await self._legacy.cancel(response_id, user_identity=user_identity)

    async def close(self) -> None:
        await self._local.close()
        if self._legacy is not None:
            await self._legacy.close()
