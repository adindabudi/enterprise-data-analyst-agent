from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from eda_runtime_state.messages import CanonicalMessage
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel


class HistoryModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        extra="ignore",
        frozen=True,
        populate_by_name=True,
        serialize_by_alias=True,
    )


class SessionPartition(HistoryModel):
    tenant_id: UUID
    owner_object_id: UUID
    session_id: str = Field(pattern=r"^ses_[A-Za-z0-9_-]{8,}$")

    def values(self) -> list[str]:
        return [str(self.tenant_id), str(self.owner_object_id), self.session_id]


class ProjectionDocument(HistoryModel):
    id: Literal["projection"] = "projection"
    record_type: Literal["projection"] = "projection"
    tenant_id: UUID
    owner_object_id: UUID
    session_id: str
    schema_version: Literal["1.0"] = "1.0"
    projection_version: int = Field(ge=1)
    summary_version: int = Field(ge=0)
    messages: tuple[dict[str, Any], ...]
    agent_session: dict[str, Any]
    projected_tokens: int = Field(ge=0)
    source_message_ids: tuple[str, ...] = ()
    updated_at: datetime
    etag: str = Field(alias="_etag", default="new")

    @field_validator("agent_session")
    @classmethod
    def reject_service_session(cls, value: dict[str, Any]) -> dict[str, Any]:
        if value.get("service_session_id") is not None:
            raise ValueError("service session IDs are not persisted")
        if "text_reasoning" in str(value) or "protected_data" in str(value):
            raise ValueError("reasoning and protected data are not persisted")
        return value


class TodoProjection(HistoryModel):
    id: Literal["todo-projection"] = "todo-projection"
    record_type: Literal["todoProjection"] = "todoProjection"
    tenant_id: UUID
    owner_object_id: UUID
    session_id: str
    items: tuple[dict[str, Any], ...]
    updated_at: datetime


__all__ = ["CanonicalMessage", "ProjectionDocument", "SessionPartition", "TodoProjection"]
