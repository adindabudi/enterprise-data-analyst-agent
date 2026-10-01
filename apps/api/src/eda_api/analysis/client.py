"""In-process task execution for the single analyst runtime.

`LocalAnalysisClient` persists the task's `run_` attempt and only then hands the task to the
supervisor, so an accepted task is durable before anything runs it.
"""

from __future__ import annotations

from typing import Protocol

from eda_runtime_state.models import TaskRecord
from eda_runtime_state.tasks import RuntimeStateConflict

from .admission import AdmissionRejected
from .attempts import AttemptStatus, TaskAttempt
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

    async def start(self, task_id: str) -> TaskAttempt:
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
        return TaskAttempt(id=attempt_id, status=AttemptStatus.QUEUED)

    async def get(self, attempt_id: str) -> TaskAttempt:
        if not is_local_attempt(attempt_id):
            # Only the retired hosted runtime made other attempts, and nothing is left to finish them.
            return TaskAttempt(id=attempt_id, status=AttemptStatus.FAILED)
        # The supervisor owns recovery of its own tasks; reconciliation must not settle them from outside.
        return TaskAttempt(id=attempt_id, status=AttemptStatus.IN_PROGRESS)

    async def cancel(self, attempt_id: str) -> TaskAttempt:
        if not is_local_attempt(attempt_id):
            return TaskAttempt(id=attempt_id, status=AttemptStatus.FAILED)
        self._supervisor.request_cancel(attempt_id)
        return TaskAttempt(id=attempt_id, status=AttemptStatus.IN_PROGRESS)

    async def close(self) -> None:
        return None


class DisabledAnalysisClient:
    """Stands in while the analysis runtime is switched off: tasks can be read, but none can start."""

    async def start(self, task_id: str) -> TaskAttempt:
        del task_id
        raise AdmissionRejected("runtime_unavailable")

    async def get(self, attempt_id: str) -> TaskAttempt:
        return TaskAttempt(id=attempt_id, status=AttemptStatus.IN_PROGRESS)

    async def cancel(self, attempt_id: str) -> TaskAttempt:
        return TaskAttempt(id=attempt_id, status=AttemptStatus.IN_PROGRESS)

    async def close(self) -> None:
        return None
