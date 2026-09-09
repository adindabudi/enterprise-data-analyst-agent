from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .scans import ScanState


def camel_case(value: str) -> str:
    words = value.split("_")
    return words[0] + "".join(word.title() for word in words[1:])


class WorkspaceSession(BaseModel):
    model_config = ConfigDict(alias_generator=camel_case, populate_by_name=True, extra="ignore", frozen=True)

    id: str
    record_type: Literal["session"] = "session"
    tenant_id: UUID
    owner_object_id: UUID
    session_id: str = Field(pattern=r"^ses_[A-Za-z0-9_-]{16,}$")
    title: str = Field(min_length=1, max_length=120)
    created_at: datetime
    last_activity_at: datetime
    expires_at: datetime
    etag: str = Field(alias="_etag", default="new")


class UploadRecord(BaseModel):
    model_config = ConfigDict(alias_generator=camel_case, populate_by_name=True, extra="ignore", frozen=True)

    id: str = Field(pattern=r"^upl_[A-Za-z0-9_-]{8,}$")
    record_type: Literal["upload"] = "upload"
    tenant_id: UUID
    owner_object_id: UUID
    session_id: str = Field(pattern=r"^ses_[A-Za-z0-9_-]{16,}$")
    blob_name: str
    blob_etag: str | None = Field(default=None, min_length=1, max_length=256)
    display_name: str = Field(min_length=1, max_length=255)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(ge=0, le=52_428_800)
    state: ScanState = "scanning"
    created_at: datetime
