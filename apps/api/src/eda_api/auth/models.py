from __future__ import annotations

import hashlib
import math
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar
from uuid import UUID

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class Principal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: UUID
    owner_object_id: UUID
    audience: UUID


class AuthFlowRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    id: str = Field(pattern=r"^flow_[A-Za-z0-9_-]{8,}$")
    record_type: str = Field(
        default="authFlow",
        validation_alias=AliasChoices("record_type", "recordType"),
        serialization_alias="recordType",
        pattern=r"^authFlow$",
    )
    flow: dict[str, Any]
    expires_at: datetime = Field(
        validation_alias=AliasChoices("expires_at", "expiresAt"), serialization_alias="expiresAt"
    )

    def to_document(self, *, now: datetime | None = None) -> dict[str, Any]:
        return {
            "id": self.id,
            "recordType": self.record_type,
            "flow": self.flow,
            "expiresAt": self.expires_at.isoformat(),
            "ttl": self.ttl_seconds(now=now),
        }

    def ttl_seconds(self, *, now: datetime | None = None) -> int:
        current_time = now or datetime.now(UTC)
        return max(1, math.ceil((self.expires_at - current_time).total_seconds()))


class AuthSessionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    id: str = Field(pattern=r"^auth_[A-Za-z0-9_-]{16,}$")
    record_type: str = Field(
        default="authSession",
        validation_alias=AliasChoices("record_type", "recordType"),
        serialization_alias="recordType",
        pattern=r"^authSession$",
    )
    tenant_id: UUID = Field(validation_alias=AliasChoices("tenant_id", "tenantId"), serialization_alias="tenantId")
    owner_object_id: UUID = Field(
        validation_alias=AliasChoices("owner_object_id", "ownerObjectId"), serialization_alias="ownerObjectId"
    )
    audience: UUID
    csrf_sha256: str = Field(
        pattern=r"^[a-f0-9]{64}$",
        validation_alias=AliasChoices("csrf_sha256", "csrfSha256"),
        serialization_alias="csrfSha256",
    )
    expires_at: datetime = Field(
        validation_alias=AliasChoices("expires_at", "expiresAt"), serialization_alias="expiresAt"
    )

    _csrf_digest_algorithm: ClassVar[str] = "sha256"

    @classmethod
    def create(
        cls,
        principal: Principal,
        csrf_token: str,
        ttl_seconds: int,
        *,
        id: str | None = None,
    ) -> AuthSessionRecord:
        return cls(
            id=id or f"auth_{secrets.token_urlsafe(32)}",
            tenant_id=principal.tenant_id,
            owner_object_id=principal.owner_object_id,
            audience=principal.audience,
            csrf_sha256=hashlib.sha256(csrf_token.encode("utf-8")).hexdigest(),
            expires_at=datetime.now(UTC) + timedelta(seconds=ttl_seconds),
        )

    def principal(self) -> Principal:
        return Principal(
            tenant_id=self.tenant_id,
            owner_object_id=self.owner_object_id,
            audience=self.audience,
        )

    def to_document(self, *, now: datetime | None = None) -> dict[str, Any]:
        return {
            "id": self.id,
            "recordType": self.record_type,
            "tenantId": str(self.tenant_id),
            "ownerObjectId": str(self.owner_object_id),
            "audience": str(self.audience),
            "csrfSha256": self.csrf_sha256,
            "expiresAt": self.expires_at.isoformat(),
            "ttl": self.ttl_seconds(now=now),
        }

    def ttl_seconds(self, *, now: datetime | None = None) -> int:
        current_time = now or datetime.now(UTC)
        return max(1, math.ceil((self.expires_at - current_time).total_seconds()))
