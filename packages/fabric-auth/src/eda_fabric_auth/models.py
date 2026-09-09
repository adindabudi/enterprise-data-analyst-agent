from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

from .crypto import CipherEnvelope


class FabricProvider(StrEnum):
    SEMANTIC_MODEL = "semantic_model"
    ONTOLOGY = "ontology"


class FabricGrantState(StrEnum):
    LINKED = "linked"
    REAUTH_REQUIRED = "reauth_required"


def _ttl_seconds(expires_at: datetime, now: datetime | None = None) -> int:
    current = now or datetime.now(UTC)
    ttl = int((expires_at - current).total_seconds())
    return 1 if ttl < 1 else ttl


class FabricGrantRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    id: str
    record_type: str = Field(
        default="fabricGrant",
        validation_alias=AliasChoices("record_type", "recordType"),
        serialization_alias="recordType",
        pattern=r"^fabricGrant$",
    )
    tenant_id: UUID = Field(validation_alias=AliasChoices("tenant_id", "tenantId"), serialization_alias="tenantId")
    owner_object_id: UUID = Field(
        validation_alias=AliasChoices("owner_object_id", "ownerObjectId"),
        serialization_alias="ownerObjectId",
    )
    provider: FabricProvider
    fabric_tenant_id: UUID = Field(
        validation_alias=AliasChoices("fabric_tenant_id", "fabricTenantId"),
        serialization_alias="fabricTenantId",
    )
    account_hash: str = Field(
        validation_alias=AliasChoices("account_hash", "accountHash"),
        serialization_alias="accountHash",
        pattern=r"^[a-f0-9]{64}$",
    )
    scope_hash: str = Field(
        validation_alias=AliasChoices("scope_hash", "scopeHash"),
        serialization_alias="scopeHash",
        pattern=r"^[a-f0-9]{64}$",
    )
    audience_hash: str = Field(
        validation_alias=AliasChoices("audience_hash", "audienceHash"),
        serialization_alias="audienceHash",
        pattern=r"^[a-f0-9]{64}$",
    )
    state: FabricGrantState = FabricGrantState.LINKED
    cache: CipherEnvelope
    last_used_at: datetime = Field(
        validation_alias=AliasChoices("last_used_at", "lastUsedAt"),
        serialization_alias="lastUsedAt",
    )
    expires_at: datetime = Field(
        validation_alias=AliasChoices("expires_at", "expiresAt"),
        serialization_alias="expiresAt",
    )
    etag: str | None = Field(default=None, validation_alias=AliasChoices("_etag", "etag"), serialization_alias="_etag")

    @model_validator(mode="after")
    def validate_provider_id(self) -> Self:
        if self.id != f"fabric-grant:{self.provider.value}":
            raise ValueError("grant ID does not match provider")
        return self

    def partition(self) -> list[str]:
        return [str(self.tenant_id), str(self.owner_object_id)]

    def to_document(self, *, now: datetime | None = None) -> dict[str, object]:
        document = self.model_dump(mode="json", by_alias=True, exclude={"etag"})
        document["ttl"] = _ttl_seconds(self.expires_at, now=now)
        return document


class FabricAuthorizationFlowRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    id: str
    record_type: str = Field(
        default="fabricAuthorizationFlow",
        validation_alias=AliasChoices("record_type", "recordType"),
        serialization_alias="recordType",
        pattern=r"^fabricAuthorizationFlow$",
    )
    flow_id: str = Field(
        validation_alias=AliasChoices("flow_id", "flowId"),
        serialization_alias="flowId",
        pattern=r"^[A-Za-z0-9_-]{8,}$",
    )
    tenant_id: UUID = Field(validation_alias=AliasChoices("tenant_id", "tenantId"), serialization_alias="tenantId")
    owner_object_id: UUID = Field(
        validation_alias=AliasChoices("owner_object_id", "ownerObjectId"),
        serialization_alias="ownerObjectId",
    )
    provider: FabricProvider
    scope_hash: str = Field(
        validation_alias=AliasChoices("scope_hash", "scopeHash"),
        serialization_alias="scopeHash",
        pattern=r"^[a-f0-9]{64}$",
    )
    audience_hash: str = Field(
        validation_alias=AliasChoices("audience_hash", "audienceHash"),
        serialization_alias="audienceHash",
        pattern=r"^[a-f0-9]{64}$",
    )
    correlation_secret_hash: str = Field(
        validation_alias=AliasChoices("correlation_secret_hash", "correlationSecretHash"),
        serialization_alias="correlationSecretHash",
        pattern=r"^[a-f0-9]{64}$",
    )
    flow: CipherEnvelope
    task_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("task_id", "taskId"),
        serialization_alias="taskId",
        min_length=1,
    )
    checkpoint_sequence: int | None = Field(
        default=None,
        validation_alias=AliasChoices("checkpoint_sequence", "checkpointSequence"),
        serialization_alias="checkpointSequence",
        ge=0,
    )
    expires_at: datetime = Field(
        validation_alias=AliasChoices("expires_at", "expiresAt"),
        serialization_alias="expiresAt",
    )
    etag: str | None = Field(default=None, validation_alias=AliasChoices("_etag", "etag"), serialization_alias="_etag")

    @classmethod
    def make_id(cls, provider: FabricProvider, flow_id: str) -> str:
        return f"fabric-flow:{provider.value}:{flow_id}"

    @model_validator(mode="after")
    def validate_provider_id(self) -> Self:
        if self.id != self.make_id(self.provider, self.flow_id):
            raise ValueError("flow ID does not match provider and flow")
        return self

    def auth_partition(self) -> str:
        return self.id

    def to_document(self, *, now: datetime | None = None) -> dict[str, object]:
        document = self.model_dump(mode="json", by_alias=True, exclude={"etag"})
        document["ttl"] = _ttl_seconds(self.expires_at, now=now)
        return document


class FabricPendingGrantRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    id: str
    record_type: str = Field(
        default="fabricPendingGrant",
        validation_alias=AliasChoices("record_type", "recordType"),
        serialization_alias="recordType",
        pattern=r"^fabricPendingGrant$",
    )
    tenant_id: UUID = Field(validation_alias=AliasChoices("tenant_id", "tenantId"), serialization_alias="tenantId")
    owner_object_id: UUID = Field(
        validation_alias=AliasChoices("owner_object_id", "ownerObjectId"),
        serialization_alias="ownerObjectId",
    )
    provider: FabricProvider
    receipt: str = Field(pattern=r"^[A-Za-z0-9_-]{8,}$")
    scope_hash: str = Field(
        validation_alias=AliasChoices("scope_hash", "scopeHash"),
        serialization_alias="scopeHash",
        pattern=r"^[a-f0-9]{64}$",
    )
    audience_hash: str = Field(
        validation_alias=AliasChoices("audience_hash", "audienceHash"),
        serialization_alias="audienceHash",
        pattern=r"^[a-f0-9]{64}$",
    )
    fabric_tenant_id: UUID = Field(
        validation_alias=AliasChoices("fabric_tenant_id", "fabricTenantId"),
        serialization_alias="fabricTenantId",
    )
    account_hash: str = Field(
        validation_alias=AliasChoices("account_hash", "accountHash"),
        serialization_alias="accountHash",
        pattern=r"^[a-f0-9]{64}$",
    )
    cache: CipherEnvelope
    task_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("task_id", "taskId"),
        serialization_alias="taskId",
        min_length=1,
    )
    checkpoint_sequence: int | None = Field(
        default=None,
        validation_alias=AliasChoices("checkpoint_sequence", "checkpointSequence"),
        serialization_alias="checkpointSequence",
        ge=0,
    )
    expires_at: datetime = Field(
        validation_alias=AliasChoices("expires_at", "expiresAt"),
        serialization_alias="expiresAt",
    )
    etag: str | None = Field(default=None, validation_alias=AliasChoices("_etag", "etag"), serialization_alias="_etag")

    @classmethod
    def make_id(cls, provider: FabricProvider, receipt: str) -> str:
        return f"fabric-pending:{provider.value}:{receipt}"

    @model_validator(mode="after")
    def validate_provider_id(self) -> Self:
        if self.id != self.make_id(self.provider, self.receipt):
            raise ValueError("pending grant ID does not match provider")
        return self

    def auth_partition(self) -> str:
        return self.id

    def to_document(self, *, now: datetime | None = None) -> dict[str, object]:
        document = self.model_dump(mode="json", by_alias=True, exclude={"etag"})
        document["ttl"] = _ttl_seconds(self.expires_at, now=now)
        return document
