from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field

from .artifacts import ArtifactRef, ArtifactStatus
from .base import ContractModel, OpaqueEventId, OpaqueSessionId, OpaqueTaskId, TimestampedContract
from .tasks import TaskStatus


class EventType(StrEnum):
    MESSAGE_DELTA = "message.delta"
    ANALYSIS_PROGRESS = "analysis_progress"
    ATTEMPT_SUPERSEDED = "attempt.superseded"
    TOOL_STARTED = "tool.started"
    TOOL_PROGRESS = "tool.progress"
    TOOL_COMPLETED = "tool.completed"
    CODE_STARTED = "code.started"
    CODE_STDOUT = "code.stdout"
    CODE_STDERR = "code.stderr"
    CODE_COMPLETED = "code.completed"
    TODO_UPDATED = "todo.updated"
    TASK_CHECKPOINTED = "task.checkpointed"
    TASK_STEERING_QUEUED = "task.steering_queued"
    PROVENANCE_ADDED = "provenance.added"
    ARTIFACT_CREATED = "artifact.created"
    ARTIFACT_VALIDATING = "artifact.validating"
    ARTIFACT_READY = "artifact.ready"
    AUTH_REQUIRED = "auth.required"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"
    RUN_CANCELLED = "run.cancelled"
    STREAM_RESUMED = "stream.resumed"


class EventBase(TimestampedContract):
    event_id: OpaqueEventId
    sequence: int = Field(ge=0)
    session_id: OpaqueSessionId
    task_id: OpaqueTaskId
    provenance_refs: tuple[str, ...] = ()


class AttemptEventBase(EventBase):
    response_attempt_id: str = Field(min_length=1, max_length=128)
    attempt_sequence: int = Field(ge=0)


class MessageDeltaPayload(ContractModel):
    delta: str = Field(min_length=1, max_length=16_384)


class MessageDeltaEvent(AttemptEventBase):
    type: Literal[EventType.MESSAGE_DELTA] = EventType.MESSAGE_DELTA
    payload: MessageDeltaPayload


ProgressState = Literal["running", "completed", "failed"]


class ProgressPayload(ContractModel):
    milestone: str = Field(min_length=1, max_length=80)
    detail: str | None = Field(default=None, max_length=500)
    # Defaulted so events stored before this field existed still replay; every emitter sets it.
    state: ProgressState = "running"


class AnalysisProgressEvent(EventBase):
    type: Literal[EventType.ANALYSIS_PROGRESS] = EventType.ANALYSIS_PROGRESS
    payload: ProgressPayload


class AttemptSupersededPayload(ContractModel):
    superseded_attempt_id: str
    replacement_attempt_id: str


class AttemptSupersededEvent(EventBase):
    type: Literal[EventType.ATTEMPT_SUPERSEDED] = EventType.ATTEMPT_SUPERSEDED
    payload: AttemptSupersededPayload


class OperationPayload(ContractModel):
    operation_id: str = Field(min_length=1, max_length=128)
    capability: str = Field(min_length=1, max_length=80)
    summary: str | None = Field(default=None, max_length=2_000)
    duration_ms: int | None = Field(default=None, ge=0)
    exit_code: int | None = None
    artifact_refs: tuple[ArtifactRef, ...] = ()


class OperationEvent(AttemptEventBase):
    type: Literal[
        EventType.TOOL_STARTED,
        EventType.TOOL_PROGRESS,
        EventType.TOOL_COMPLETED,
        EventType.CODE_STARTED,
        EventType.CODE_STDOUT,
        EventType.CODE_STDERR,
        EventType.CODE_COMPLETED,
    ]
    payload: OperationPayload


class TodoItem(ContractModel):
    todo_id: str
    text: str = Field(min_length=1, max_length=500)
    completed: bool


class TodoUpdatedEvent(EventBase):
    type: Literal[EventType.TODO_UPDATED] = EventType.TODO_UPDATED
    payload: tuple[TodoItem, ...]


class TaskCheckpointPayload(ContractModel):
    status: TaskStatus
    checkpoint_sequence: int = Field(ge=0)


class TaskCheckpointedEvent(EventBase):
    type: Literal[EventType.TASK_CHECKPOINTED] = EventType.TASK_CHECKPOINTED
    payload: TaskCheckpointPayload


class SteeringQueuedPayload(ContractModel):
    command_id: str
    inbox_sequence: int = Field(ge=1)
    label: Literal["Queued for next checkpoint"] = "Queued for next checkpoint"


class SteeringQueuedEvent(EventBase):
    type: Literal[EventType.TASK_STEERING_QUEUED] = EventType.TASK_STEERING_QUEUED
    payload: SteeringQueuedPayload


class ProvenanceAddedPayload(ContractModel):
    reference_id: str
    reference_kind: Literal["input", "query", "execution", "artifact", "claim"]


class ProvenanceAddedEvent(EventBase):
    type: Literal[EventType.PROVENANCE_ADDED] = EventType.PROVENANCE_ADDED
    payload: ProvenanceAddedPayload


class ArtifactEventPayload(ContractModel):
    artifact: ArtifactRef
    status: ArtifactStatus


class ArtifactEvent(EventBase):
    type: Literal[EventType.ARTIFACT_CREATED, EventType.ARTIFACT_VALIDATING, EventType.ARTIFACT_READY]
    payload: ArtifactEventPayload


class AuthRequiredPayload(ContractModel):
    action_path: Literal["/api/fabric/auth/start"] = "/api/fabric/auth/start"


class AuthRequiredEvent(EventBase):
    type: Literal[EventType.AUTH_REQUIRED] = EventType.AUTH_REQUIRED
    payload: AuthRequiredPayload


class RunTerminalPayload(ContractModel):
    status: TaskStatus
    final_message_id: str | None = None
    diagnostic_ref: ArtifactRef | None = None


class RunTerminalEvent(EventBase):
    type: Literal[EventType.RUN_COMPLETED, EventType.RUN_FAILED, EventType.RUN_CANCELLED]
    payload: RunTerminalPayload


class StreamResumedPayload(ContractModel):
    last_durable_sequence: int = Field(ge=0)
    omitted_from_sequence: int | None = Field(default=None, ge=0)
    omitted_to_sequence: int | None = Field(default=None, ge=0)


class StreamResumedEvent(EventBase):
    type: Literal[EventType.STREAM_RESUMED] = EventType.STREAM_RESUMED
    payload: StreamResumedPayload


ActivityEvent = Annotated[
    MessageDeltaEvent
    | AnalysisProgressEvent
    | AttemptSupersededEvent
    | OperationEvent
    | TodoUpdatedEvent
    | TaskCheckpointedEvent
    | SteeringQueuedEvent
    | ProvenanceAddedEvent
    | ArtifactEvent
    | AuthRequiredEvent
    | RunTerminalEvent
    | StreamResumedEvent,
    Field(discriminator="type"),
]
