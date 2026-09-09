from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from eda_worker.fabric.ontology.contracts import OntologyQueryPurpose


class FabricOntologyPublicRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    provider: Literal["ontology"] = "ontology"
    query_ref: str = Field(alias="queryRef", pattern=r"^[A-Za-z0-9_-]{1,128}$")
    source_alias: str = Field(alias="sourceAlias", pattern=r"^[a-z][a-z0-9-]{1,39}$")
    purpose: OntologyQueryPurpose
    entity_schema_digest: str = Field(alias="entitySchemaDigest", pattern=r"^[a-f0-9]{64}$")
    result_artifact_ref: str = Field(alias="resultArtifactRef", pattern=r"^artifact[-_][A-Za-z0-9_-]{8,}$")
    result_sha256: str = Field(alias="resultSha256", pattern=r"^[a-f0-9]{64}$")
    result_shape: str = Field(alias="resultShape", pattern=r"^[a-z_]{1,64}$")
    reconciliation_status: Literal["not_required", "matched", "mismatched"] = Field(
        alias="reconciliationStatus",
        default="not_required",
    )
    completed_at: datetime = Field(alias="completedAt")


class FabricOntologyQueryDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    tenant_id: UUID = Field(alias="tenantId")
    owner_object_id: UUID = Field(alias="ownerObjectId")
    session_id: str = Field(alias="sessionId", pattern=r"^ses_[A-Za-z0-9_-]{8,}$")
    query_ref: str = Field(alias="queryRef", pattern=r"^[A-Za-z0-9_-]{1,128}$")
    source_alias: str = Field(alias="sourceAlias", pattern=r"^[a-z][a-z0-9-]{1,39}$")
    workspace_id: UUID = Field(alias="workspaceId")
    ontology_id: UUID = Field(alias="ontologyId")
    purpose: OntologyQueryPurpose
    question: str = Field(min_length=3, max_length=4_000)
    provider_contract_digest: str = Field(alias="providerContractDigest", pattern=r"^[a-f0-9]{64}$")
    entity_schema_digest: str = Field(alias="entitySchemaDigest", pattern=r"^[a-f0-9]{64}$")
    result_artifact_ref: str = Field(alias="resultArtifactRef", pattern=r"^artifact[-_][A-Za-z0-9_-]{8,}$")
    result_sha256: str = Field(alias="resultSha256", pattern=r"^[a-f0-9]{64}$")
    result_shape: str = Field(alias="resultShape", pattern=r"^[a-z_]{1,64}$")
    reconciliation_status: Literal["not_required", "matched", "mismatched"] = Field(
        alias="reconciliationStatus",
        default="not_required",
    )
    started_at: datetime = Field(alias="startedAt")
    completed_at: datetime = Field(alias="completedAt")

    def public_record(self) -> FabricOntologyPublicRecord:
        return FabricOntologyPublicRecord(
            queryRef=self.query_ref,
            sourceAlias=self.source_alias,
            purpose=self.purpose,
            entitySchemaDigest=self.entity_schema_digest,
            resultArtifactRef=self.result_artifact_ref,
            resultSha256=self.result_sha256,
            resultShape=self.result_shape,
            reconciliationStatus=self.reconciliation_status,
            completedAt=self.completed_at,
        )
