"""One execution attempt of a task, as the task service sees it."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class AttemptStatus(StrEnum):
    QUEUED = "queued"
    IN_PROGRESS = "in_progress"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass(frozen=True)
class TaskAttempt:
    id: str
    status: AttemptStatus


class TaskExecutor(Protocol):
    async def start(self, task_id: str) -> TaskAttempt: ...

    async def get(self, attempt_id: str) -> TaskAttempt: ...

    async def cancel(self, attempt_id: str) -> TaskAttempt: ...

    async def close(self) -> object: ...
