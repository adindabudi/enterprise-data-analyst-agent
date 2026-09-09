from __future__ import annotations

from datetime import datetime
from typing import Literal

from eda_worker.acceptance.fabric import FABRIC_ACCEPTANCE_TESTS
from pydantic import BaseModel, ConfigDict, Field, model_validator


class FabricCloudObservations(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    provider: Literal["semantic_model"] = "semantic_model"
    topology: Literal["cross_tenant"] = "cross_tenant"
    run_id: str = Field(alias="runId", pattern=r"^run_[A-Za-z0-9_-]{8,}$")
    deployment_id: str = Field(alias="deploymentId", min_length=1, max_length=128)
    provider_contract_sha256: str = Field(alias="providerContractSha256", pattern=r"^[a-f0-9]{64}$")
    model_profile: Literal["gpt-5.6-terra-medium-v1"] = Field(alias="modelProfile")
    served_model: Literal["gpt-5.6-terra"] = Field(alias="servedModel")
    served_snapshot: Literal["2026-07-09"] = Field(alias="servedSnapshot")
    prompt_version: Literal["gpt-5.6-terra-v1"] = Field(alias="promptVersion")
    prompt_sha256: str = Field(alias="promptSha256", pattern=r"^[a-f0-9]{64}$")
    request_options_sha256: str = Field(alias="requestOptionsSha256", pattern=r"^[a-f0-9]{64}$")
    tenant_hashes: tuple[str, str] = Field(alias="tenantHashes")
    provider_tool_names: tuple[str, ...] = Field(alias="providerToolNames")
    runtime_tool_names: tuple[str, ...] = Field(alias="runtimeToolNames")
    invoked_tool_names: tuple[str, ...] = Field(alias="invokedToolNames")
    rls_result_hashes: tuple[str, str] = Field(alias="rlsResultHashes")
    build_denial_code: Literal["build_permission_denied"] = Field(alias="buildDenialCode")
    silent_refresh: Literal["passed"] = Field(alias="silentRefresh")
    revoked_refresh: Literal["reauth_required"] = Field(alias="revokedRefresh")
    unlink_relink: Literal["passed"] = Field(alias="unlinkRelink")
    forced_first_routing: Literal["passed"] = Field(alias="forcedFirstRouting")
    automatic_routing: Literal["passed"] = Field(alias="automaticRouting")
    provenance_reconciled: bool = Field(alias="provenanceReconciled")
    forbidden_markers_found: Literal[0] = Field(alias="forbiddenMarkersFound")
    grant_ciphertext_verified: bool = Field(alias="grantCiphertextVerified")
    fallback_paths_observed: tuple[str, ...] = Field(alias="fallbackPathsObserved", max_length=0)
    controls: dict[str, Literal["passed", "failed"]]
    observed_at: datetime = Field(alias="observedAt")

    @model_validator(mode="after")
    def validate_complete_observations(self) -> FabricCloudObservations:
        if frozenset(self.controls) != FABRIC_ACCEPTANCE_TESTS or any(
            value != "passed" for value in self.controls.values()
        ):
            raise ValueError("cloud observations contain missing, extra, or failed controls")
        hashes = (*self.tenant_hashes, *self.rls_result_hashes)
        if any(len(value) != 64 or any(character not in "0123456789abcdef" for character in value) for value in hashes):
            raise ValueError("cloud observation hashes are malformed")
        if len(set(self.tenant_hashes)) != 2 or len(set(self.rls_result_hashes)) != 2:
            raise ValueError("cross-tenant and RLS observations must be distinct")
        return self
