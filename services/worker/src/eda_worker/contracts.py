from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class WorkerModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class AnalysisWorkflowInput(WorkerModel):
    task_id: str = Field(alias="taskId", pattern=r"^task_[A-Za-z0-9_-]{8,}$")


class AgentOutcome(StrEnum):
    CONTINUE = "continue"
    BLOCKED_AUTH = "blocked_auth"
    FAILED = "failed"


class AgentPhaseResult(WorkerModel):
    outcome: AgentOutcome
    applied_command_sequence: int = Field(default=0, ge=0, alias="appliedCommandSequence")
    failure_code: str | None = Field(default=None, alias="failureCode")


class ValidationOutcome(StrEnum):
    PASSED = "passed"
    REPAIRABLE = "repairable"
    FAILED = "failed"


class ValidationResult(WorkerModel):
    outcome: ValidationOutcome
    report_ref: str = Field(alias="reportRef")


class PublicationResult(WorkerModel):
    final_message_id: str = Field(alias="finalMessageId")


class ControlSnapshot(WorkerModel):
    cancellation_requested: bool = Field(alias="cancellationRequested")
    pending_command_ids: tuple[str, ...] = Field(default=(), alias="pendingCommandIds")
    highest_command_sequence: int = Field(default=0, ge=0, alias="highestCommandSequence")
    auth_resumed: bool = Field(default=False, alias="authResumed")
