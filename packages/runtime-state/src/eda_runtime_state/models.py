from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID

from eda_contracts import ArtifactKind, ArtifactRef
from eda_contracts.controls import CommandKind
from eda_contracts.tasks import TaskStatus
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel


class RuntimeModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        extra="ignore",
        frozen=True,
        populate_by_name=True,
        serialize_by_alias=True,
    )


class TaskPartition(RuntimeModel):
    tenant_id: UUID
    owner_object_id: UUID
    session_id: str = Field(pattern=r"^ses_[A-Za-z0-9_-]{8,}$")

    def values(self) -> list[str]:
        return [str(self.tenant_id), str(self.owner_object_id), self.session_id]


class QueryResultRef(RuntimeModel):
    """One source query the chat ran, and the artifact holding the rows it returned."""

    artifact_id: str = Field(min_length=1, max_length=128)
    version: int = Field(ge=1)
    kind: str = Field(min_length=1, max_length=32)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    display_name: str = Field(min_length=1, max_length=255)
    # The query itself is kept so the rows can be traced back to what asked for them.
    query: str = Field(min_length=1, max_length=8_000)
    query_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    row_count: int = Field(ge=0)
    source_alias: str | None = Field(default=None, max_length=64)
    # Which turn asked. The ledger outlives a turn, so executed_at alone cannot say.
    # Optional because rows recorded before this field existed have none.
    message_id: str | None = Field(default=None, max_length=128)
    executed_at: datetime


class RequiredOutput(RuntimeModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal[
        ArtifactKind.HTML, ArtifactKind.XLSX, ArtifactKind.XLSM, ArtifactKind.DOCX,
        ArtifactKind.PPTX, ArtifactKind.PDF, ArtifactKind.PNG, ArtifactKind.SVG, ArtifactKind.MERMAID,
    ]
    minimum_count: int = Field(default=1, ge=1, le=10)


class TaskRecord(RuntimeModel):
    id: str = Field(pattern=r"^task_[A-Za-z0-9_-]{8,}$")
    record_type: Literal["task"] = "task"
    tenant_id: UUID
    owner_object_id: UUID
    session_id: str
    status: TaskStatus
    checkpoint_sequence: int = Field(ge=0)
    command_sequence: int = Field(ge=0)
    applied_command_sequence: int = Field(ge=0)
    cancellation_requested: bool = False
    initial_dispatch_claimed: bool = False
    active_attempt_id: str | None = None
    active_sandbox_id: str | None = None
    source_message_id: str | None = Field(default=None, alias="sourceMessageId")
    handoff_context: str | None = Field(default=None, max_length=16_000)
    # execute_in_sandbox accepts at most ten input artifacts, so carrying more would be unusable.
    query_results: tuple[QueryResultRef, ...] = Field(default=(), max_length=10)
    input_upload_ids: tuple[Annotated[str, Field(pattern=r"^upl_[A-Za-z0-9_-]{8,}$", max_length=128)], ...] = Field(
        default=(), max_length=10
    )
    input_artifacts: tuple[ArtifactRef, ...] = Field(default=(), max_length=10)
    required_outputs: tuple[RequiredOutput, ...] | None = Field(default=None, max_length=9)
    required_outputs_sequence: int = Field(default=0, ge=0)
    final_message_id: str | None = None
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    etag: str = Field(alias="_etag", default="new")

    @field_validator("input_artifacts")
    @classmethod
    def validate_input_kinds(cls, refs: tuple[ArtifactRef, ...]) -> tuple[ArtifactRef, ...]:
        if any(ref.kind is not ArtifactKind.INPUT for ref in refs):
            raise ValueError("task inputs must be input artifacts")
        return refs

    @field_validator("required_outputs")
    @classmethod
    def validate_required_outputs(cls, outputs: tuple[RequiredOutput, ...] | None) -> tuple[RequiredOutput, ...] | None:
        if outputs is not None and len({item.kind for item in outputs}) != len(outputs):
            raise ValueError("required output kinds must be unique")
        return outputs

    def partition(self) -> TaskPartition:
        return TaskPartition(
            tenant_id=self.tenant_id,
            owner_object_id=self.owner_object_id,
            session_id=self.session_id,
        )


class RuntimeLocator(RuntimeModel):
    id: str
    record_type: Literal["runtimeLocator"] = "runtimeLocator"
    tenant_id: UUID
    owner_object_id: UUID
    session_id: str
    expires_at: datetime
    ttl: int = Field(ge=1)

    def partition(self) -> TaskPartition:
        return TaskPartition(
            tenant_id=self.tenant_id,
            owner_object_id=self.owner_object_id,
            session_id=self.session_id,
        )


class TaskCommand(RuntimeModel):
    id: str = Field(pattern=r"^cmd_[A-Za-z0-9_-]{8,}$")
    record_type: Literal["taskCommand"] = "taskCommand"
    tenant_id: UUID
    owner_object_id: UUID
    session_id: str
    task_id: str
    sequence: int = Field(ge=1)
    kind: CommandKind
    text: str | None = Field(default=None, max_length=4000)
    request_id: str | None = None
    approved: bool | None = None
    created_at: datetime


class OperationStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class OperationRecord(RuntimeModel):
    id: str = Field(pattern=r"^op_[a-f0-9]{64}$")
    record_type: Literal["operation"] = "operation"
    tenant_id: UUID
    owner_object_id: UUID
    session_id: str
    task_id: str
    step_name: str
    canonical_input_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    status: OperationStatus
    immutable_result_ref: str | None = None
    provider_call_id: str | None = None
    attempt_count: int = Field(default=1, ge=1)
    updated_at: datetime
    etag: str = Field(alias="_etag", default="new")


class OperationKeyInput(RuntimeModel):
    workflow_instance_id: str
    step_name: str
    canonical_input: dict[str, Any]


class InvalidTransition(ValueError):
    pass


TERMINAL = {
    TaskStatus.COMPLETED,
    TaskStatus.CANCELLED,
    TaskStatus.FAILED,
    TaskStatus.FAILED_CANCELLATION,
}

ALLOWED: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.PLANNING: {
        TaskStatus.ACQUIRING_DATA,
        TaskStatus.ANALYZING,
        TaskStatus.BLOCKED_AUTH,
        TaskStatus.CANCELLING,
        TaskStatus.FAILED,
    },
    TaskStatus.ACQUIRING_DATA: {
        TaskStatus.ANALYZING,
        TaskStatus.BLOCKED_AUTH,
        TaskStatus.CANCELLING,
        TaskStatus.FAILED,
    },
    TaskStatus.BLOCKED_AUTH: {TaskStatus.ACQUIRING_DATA, TaskStatus.CANCELLING, TaskStatus.FAILED},
    TaskStatus.ANALYZING: {
        TaskStatus.GENERATING,
        TaskStatus.COMPLETED,
        TaskStatus.CANCELLING,
        TaskStatus.FAILED,
    },
    TaskStatus.GENERATING: {TaskStatus.VALIDATING, TaskStatus.CANCELLING, TaskStatus.FAILED},
    TaskStatus.VALIDATING: {TaskStatus.GENERATING, TaskStatus.PUBLISHING, TaskStatus.CANCELLING, TaskStatus.FAILED},
    TaskStatus.PUBLISHING: {TaskStatus.COMPLETED, TaskStatus.CANCELLING, TaskStatus.FAILED},
    TaskStatus.CANCELLING: {TaskStatus.CANCELLED, TaskStatus.FAILED_CANCELLATION},
}


def transition(current: TaskStatus, next_status: TaskStatus) -> TaskStatus:
    if current in TERMINAL or next_status not in ALLOWED.get(current, set()):
        raise InvalidTransition(f"invalid task transition: {current.value} -> {next_status.value}")
    return next_status


def canonical_json(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()


def canonical_operation_key(value: OperationKeyInput) -> str:
    input_hash = hashlib.sha256(canonical_json(value.canonical_input)).hexdigest()
    material = f"{value.workflow_instance_id}\0{value.step_name}\0{input_hash}".encode()
    return f"op_{hashlib.sha256(material).hexdigest()}"


def deterministic_task_id(partition: TaskPartition, idempotency_key: str) -> str:
    material = "\0".join([*partition.values(), idempotency_key]).encode()
    digest = base64.urlsafe_b64encode(hashlib.sha256(material).digest()).decode().rstrip("=")
    return f"task_{digest[:32]}"
