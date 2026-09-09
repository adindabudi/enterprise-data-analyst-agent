from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Literal

from eda_worker.acceptance.fabric import FABRIC_ACCEPTANCE_TESTS, FabricAcceptanceResult, acceptance_passed
from eda_worker.fabric.readiness import FabricFeatureRecord
from pydantic import BaseModel, ConfigDict, Field, model_validator

REQUIRED_TESTS = FABRIC_ACCEPTANCE_TESTS


class StateModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class FabricAcceptanceManifest(StateModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    provider: Literal["semantic_model"] = "semantic_model"
    state: Literal["passed"] = "passed"
    topology: Literal["cross_tenant"] = "cross_tenant"
    run_id: str = Field(alias="runId", pattern=r"^run_[A-Za-z0-9_-]{8,}$")
    deployment_id: str = Field(alias="deploymentId", min_length=1, max_length=128)
    provider_contract_sha256: str = Field(alias="providerContractSha256", pattern=r"^[a-f0-9]{64}$")
    model_profile: str = Field(alias="modelProfile", min_length=1, max_length=80)
    served_model: str = Field(alias="servedModel", min_length=1, max_length=128)
    served_snapshot: str | None = Field(default=None, alias="servedSnapshot", max_length=64)
    prompt_version: str = Field(alias="promptVersion", min_length=1, max_length=128)
    prompt_sha256: str = Field(alias="promptSha256", pattern=r"^[a-f0-9]{64}$")
    request_options_sha256: str = Field(alias="requestOptionsSha256", pattern=r"^[a-f0-9]{64}$")
    tenant_hashes: tuple[str, str] = Field(alias="tenantHashes")
    tests: dict[str, Literal["passed", "failed"]]
    started_at: datetime = Field(alias="startedAt")
    completed_at: datetime = Field(alias="completedAt")

    @model_validator(mode="after")
    def validate_complete_evidence(self) -> FabricAcceptanceManifest:
        if frozenset(self.tests) != REQUIRED_TESTS or any(value != "passed" for value in self.tests.values()):
            raise ValueError("Fabric acceptance manifest tests are incomplete or failed")
        if len(set(self.tenant_hashes)) != 2 or any(not _is_sha256(value) for value in self.tenant_hashes):
            raise ValueError("Fabric acceptance requires two distinct tenant hashes")
        if self.completed_at < self.started_at:
            raise ValueError("Fabric acceptance timestamps are invalid")
        return self


def acceptance_manifest_sha256(manifest: FabricAcceptanceManifest) -> str:
    return hashlib.sha256(
        json.dumps(
            manifest.model_dump(mode="json", by_alias=True),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    ).hexdigest()


def validate_promotion(
    *,
    active_principal_id: str,
    expected_principal_id: str,
    feature: FabricFeatureRecord,
    result: FabricAcceptanceResult,
    manifest: FabricAcceptanceManifest,
) -> FabricFeatureRecord:
    if active_principal_id.casefold() != expected_principal_id.casefold():
        raise ValueError("active principal is not the Fabric acceptance principal")
    if feature.state != "configured" or feature.acceptance_evidence_sha256 is not None:
        raise ValueError("Fabric feature state is not promotable")
    if not acceptance_passed(result):
        raise ValueError("Fabric acceptance job result did not pass")
    bindings = {
        "run": (result.run_id, manifest.run_id),
        "deployment": (feature.deployment_id, manifest.deployment_id),
        "result deployment": (result.deployment_id, manifest.deployment_id),
        "provider contract": (feature.provider_contract_sha256, manifest.provider_contract_sha256),
        "result provider contract": (result.provider_contract_sha256, manifest.provider_contract_sha256),
        "model profile": (feature.model_profile, manifest.model_profile),
        "served model": (feature.served_model, manifest.served_model),
        "served snapshot": (feature.served_snapshot, manifest.served_snapshot),
        "prompt version": (feature.prompt_version, manifest.prompt_version),
        "prompt hash": (feature.prompt_sha256, manifest.prompt_sha256),
        "request options": (feature.request_options_sha256, manifest.request_options_sha256),
    }
    for label, (actual, expected) in bindings.items():
        if actual != expected:
            raise ValueError(f"Fabric acceptance {label} does not match")
    return feature.model_copy(
        update={
            "state": "ready",
            "acceptance_evidence_sha256": acceptance_manifest_sha256(manifest),
            "verified_at": datetime.now(UTC),
        }
    )


def clean_cosmos_document(value: Mapping[str, object]) -> dict[str, object]:
    return {key: item for key, item in value.items() if not key.startswith("_")}


def _is_sha256(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    return all(character in "0123456789abcdef" for character in value)
