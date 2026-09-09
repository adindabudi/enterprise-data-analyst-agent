from __future__ import annotations

from datetime import datetime
from typing import Literal

from eda_contracts.base import ContractModel, OpaqueArtifactId, OpaqueTaskId, Sha256Hex
from pydantic import Field, model_validator


class ModelRecord(ContractModel):
    provider: Literal["foundry"]
    model_profile: str = Field(pattern=r"^[a-z0-9][a-z0-9.-]{2,79}$")
    base_model: str
    deployment: str
    hosting: Literal["azure", "anthropic"]
    effort: Literal["medium", "high", "xhigh"]
    reasoning_mode: Literal["adaptive", "standard", "disabled"]
    prompt_template_version: str
    prompt_sha256: Sha256Hex
    request_options_sha256: Sha256Hex
    context_snapshot_version: int = Field(ge=1)


class ManifestArtifact(ContractModel):
    artifact_id: OpaqueArtifactId
    version: int = Field(ge=1)
    sha256: Sha256Hex


class OutputArtifact(ManifestArtifact):
    status: Literal["ready", "rejected", "incomplete"]


class FabricQueryRecord(ContractModel):
    query_id: str = Field(pattern=r"^fabric-query-[A-Za-z0-9_-]+$")
    source_alias: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9-]{1,39}$")
    purpose: Literal["schema", "aggregate", "control_total"] | None = None
    semantic_model_id: str
    query_sha256: Sha256Hex
    provider_schema_sha256: Sha256Hex | None = None
    result_artifact_id: OpaqueArtifactId
    result_sha256: Sha256Hex | None = None
    result_shape: str | None = Field(default=None, pattern=r"^[a-z_]{1,64}$")
    row_count: int = Field(ge=0)
    attempts: int = Field(default=1, ge=1, le=16)
    reconciliation_status: Literal["not_required", "pending", "matched", "mismatched"] = "not_required"
    executed_at: datetime


class ExecutionRecord(ContractModel):
    execution_id: str
    script_artifact_id: OpaqueArtifactId
    script_sha256: Sha256Hex
    parameters: dict[str, str | int | float | bool | None]
    runtime_image_digest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    exit_code: int


class CheckRecord(ContractModel):
    schema_valid: bool
    key_totals_reconciled: bool
    missing_values_reviewed: bool


class ClaimReference(ContractModel):
    claim_id: str
    query_ids: tuple[str, ...] = ()
    input_refs: tuple[str, ...] = ()
    execution_ids: tuple[str, ...] = ()
    output_refs: tuple[str, ...] = ()


class TaskManifest(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    task_id: OpaqueTaskId
    model: ModelRecord
    inputs: tuple[ManifestArtifact, ...]
    fabric_queries: tuple[FabricQueryRecord, ...]
    executions: tuple[ExecutionRecord, ...]
    checks: CheckRecord
    outputs: tuple[OutputArtifact, ...]
    claims: tuple[ClaimReference, ...] = ()
    created_at: datetime

    @model_validator(mode="after")
    def _reject_credential_shaped_keys(self) -> TaskManifest:
        from .validate import walk_for_forbidden_keys

        walk_for_forbidden_keys(self.model_dump(mode="json", by_alias=True))
        return self
