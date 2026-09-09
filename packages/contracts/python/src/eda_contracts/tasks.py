from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from .base import ContractModel, OpaqueSessionId, OpaqueTaskId


class TaskStatus(StrEnum):
    PLANNING = "planning"
    ACQUIRING_DATA = "acquiring_data"
    ANALYZING = "analyzing"
    GENERATING = "generating"
    VALIDATING = "validating"
    PUBLISHING = "publishing"
    BLOCKED_AUTH = "blocked_auth"
    CANCELLING = "cancelling"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    FAILED_CANCELLATION = "failed_cancellation"


class TaskSummary(ContractModel):
    task_id: OpaqueTaskId
    session_id: OpaqueSessionId
    status: TaskStatus
    checkpoint_sequence: int = Field(ge=0)
    active_attempt_id: str | None = None
    status_detail: str | None = Field(default=None, max_length=500)
