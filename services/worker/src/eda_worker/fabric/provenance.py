from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal, Protocol, cast
from uuid import UUID

from azure.cosmos.aio import ContainerProxy
from azure.cosmos.exceptions import CosmosHttpResponseError
from eda_contracts import ArtifactKind, ArtifactRef
from eda_provenance.models import FabricQueryRecord
from eda_runtime_state.models import TaskRecord
from pydantic import BaseModel, ConfigDict, Field, model_validator

from eda_worker.fabric.config import SemanticModelTarget
from eda_worker.fabric.contracts import FabricQueryOperation, FabricQueryPurpose
from eda_worker.fabric.planner import FabricPlannerResult

type ReconciliationStatus = Literal["not_required", "pending", "matched", "mismatched"]


class ProvenanceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True, serialize_by_alias=True)


class FabricQueryDocument(ProvenanceModel):
    record_type: Literal["fabricQuery"] = Field(default="fabricQuery", alias="recordType")
    tenant_id: UUID = Field(alias="tenantId")
    owner_object_id: UUID = Field(alias="ownerObjectId")
    session_id: str = Field(alias="sessionId", pattern=r"^ses_[A-Za-z0-9_-]{8,}$")
    task_id: str = Field(alias="taskId", pattern=r"^task_[A-Za-z0-9_-]{8,}$")
    query_ref: str = Field(alias="queryRef", pattern=r"^fabric-query-[A-Za-z0-9_-]+$")
    source_alias: str = Field(alias="sourceAlias", pattern=r"^[a-z][a-z0-9-]{1,39}$")
    purpose: FabricQueryPurpose
    question: str = Field(min_length=1, max_length=4_000)
    semantic_model_id: UUID = Field(alias="semanticModelId")
    provider_schema_sha256: str = Field(alias="providerSchemaSha256", pattern=r"^[a-f0-9]{64}$")
    generated_dax: tuple[str, ...] = Field(alias="generatedDax", max_length=8)
    attempts: int = Field(ge=1, le=16)
    result_artifact: ArtifactRef = Field(alias="resultArtifact")
    result_sha256: str = Field(alias="resultSha256", pattern=r"^[a-f0-9]{64}$")
    result_shape: str = Field(alias="resultShape", pattern=r"^[a-z_]{1,64}$")
    row_count: int | None = Field(default=None, alias="rowCount", ge=0)
    units: tuple[str, ...] = Field(default=(), max_length=32)
    filters: tuple[str, ...] = Field(default=(), max_length=64)
    reconciliation_status: ReconciliationStatus = Field(alias="reconciliationStatus")
    started_at: datetime = Field(alias="startedAt")
    completed_at: datetime = Field(alias="completedAt")

    @model_validator(mode="after")
    def validate_evidence(self) -> FabricQueryDocument:
        if self.completed_at < self.started_at:
            raise ValueError("Fabric query completion precedes its start")
        if self.result_sha256 != self.result_artifact.sha256:
            raise ValueError("Fabric result artifact digest does not match query evidence")
        if self.purpose is not FabricQueryPurpose.SCHEMA and not self.generated_dax:
            raise ValueError("Fabric data query evidence requires generated DAX")
        return self

    def to_document(self) -> dict[str, object]:
        return {"id": self.query_ref, **self.model_dump(mode="json", by_alias=True)}

    def public_record(self) -> FabricQueryRecord:
        return FabricQueryRecord(
            query_id=self.query_ref,
            source_alias=self.source_alias,
            purpose=self.purpose.value,
            semantic_model_id=str(self.semantic_model_id),
            query_sha256=_sha256_json(list(self.generated_dax)),
            provider_schema_sha256=self.provider_schema_sha256,
            result_artifact_id=self.result_artifact.artifact_id,
            result_sha256=self.result_sha256,
            result_shape=self.result_shape,
            row_count=self.row_count or 0,
            attempts=self.attempts,
            reconciliation_status=self.reconciliation_status,
            executed_at=self.completed_at,
        )


class FabricArtifactWriter(Protocol):
    async def persist_bytes(
        self,
        task_id: str,
        kind: ArtifactKind,
        display_name: str,
        content: bytes,
    ) -> ArtifactRef: ...


class FabricQueryRepository(Protocol):
    async def put(self, document: FabricQueryDocument) -> FabricQueryDocument: ...


class InMemoryFabricQueryRepository:
    def __init__(self) -> None:
        self._documents: dict[tuple[UUID, UUID, str, str], FabricQueryDocument] = {}

    async def put(self, document: FabricQueryDocument) -> FabricQueryDocument:
        key = document.tenant_id, document.owner_object_id, document.session_id, document.query_ref
        existing = self._documents.get(key)
        if existing is not None and existing != document:
            raise ValueError("Fabric query evidence conflict")
        self._documents[key] = document
        return document

    async def get(self, task: TaskRecord, query_ref: str) -> FabricQueryDocument | None:
        return self._documents.get((task.tenant_id, task.owner_object_id, task.session_id, query_ref))


class CosmosFabricQueryRepository:
    def __init__(self, workspace: ContainerProxy) -> None:
        self._workspace = workspace

    async def put(self, document: FabricQueryDocument) -> FabricQueryDocument:
        try:
            stored = await self._workspace.create_item(document.to_document())
        except CosmosHttpResponseError as error:
            if error.status_code != 409:
                raise
            stored = await self._workspace.read_item(
                item=document.query_ref,
                partition_key=[str(document.tenant_id), str(document.owner_object_id), document.session_id],
            )
            existing = FabricQueryDocument.model_validate(stored)
            if existing != document:
                raise ValueError("Fabric query evidence conflict") from error
            return existing
        return FabricQueryDocument.model_validate(cast(Any, stored))


class FabricQueryRecorder:
    def __init__(
        self,
        *,
        artifact_store: FabricArtifactWriter,
        repository: FabricQueryRepository,
        administrator_control_sha256: Mapping[str, str] | None = None,
    ) -> None:
        self._artifact_store = artifact_store
        self._repository = repository
        self._administrator_control_sha256 = dict(administrator_control_sha256 or {})

    async def record(
        self,
        *,
        task: TaskRecord,
        query_ref: str,
        operation: FabricQueryOperation,
        target: SemanticModelTarget,
        planned: FabricPlannerResult,
        started_at: datetime,
        completed_at: datetime,
    ) -> FabricQueryDocument:
        if planned.provider_schema_sha256 is None:
            raise ValueError("Fabric planner returned no provider schema digest")
        if planned.result_shape is None:
            raise ValueError("Fabric planner returned no structural result shape")
        result_bytes = _canonical_json_bytes(planned.result)
        result_artifact = await self._artifact_store.persist_bytes(
            task.id,
            ArtifactKind.DATA,
            "fabric-query-result.json",
            result_bytes,
        )
        result_sha256 = hashlib.sha256(result_bytes).hexdigest()
        if result_artifact.sha256 != result_sha256:
            raise ValueError("Fabric immutable result artifact digest mismatch")
        document = FabricQueryDocument(
            tenantId=task.tenant_id,
            ownerObjectId=task.owner_object_id,
            sessionId=task.session_id,
            taskId=task.id,
            queryRef=query_ref,
            sourceAlias=operation.semantic_model,
            purpose=operation.purpose,
            question=" ".join(operation.question.split()),
            semanticModelId=target.model_id,
            providerSchemaSha256=planned.provider_schema_sha256,
            generatedDax=planned.dax_queries,
            attempts=max(1, len(planned.provider_calls)),
            resultArtifact=result_artifact,
            resultSha256=result_sha256,
            resultShape=planned.result_shape,
            rowCount=planned.row_count,
            reconciliationStatus=self._reconciliation_status(operation, result_sha256),
            startedAt=started_at,
            completedAt=completed_at,
        )
        return await self._repository.put(document)

    def _reconciliation_status(
        self,
        operation: FabricQueryOperation,
        result_sha256: str,
    ) -> ReconciliationStatus:
        if operation.purpose is not FabricQueryPurpose.CONTROL_TOTAL:
            return "not_required"
        expected = self._administrator_control_sha256.get(operation.semantic_model)
        if expected is None:
            return "pending"
        return "matched" if expected == result_sha256 else "mismatched"


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _sha256_json(value: object) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()
