from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AcceptanceEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    provider: Literal["ontology"]
    state: Literal["configured"]
    topology: Literal["cross_tenant"]
    provider_contract_digest: str = Field(alias="providerContractDigest", pattern=r"^[a-f0-9]{64}$")
    auth_contract_digest: str = Field(alias="authContractDigest", pattern=r"^[a-f0-9]{64}$")
    run_id: str = Field(alias="runId", pattern=r"^run_[A-Za-z0-9_-]{8,}$")


def validate_ontology_acceptance(evidence: AcceptanceEvidence) -> AcceptanceEvidence:
    return evidence


ONTOLOGY_ACCEPTANCE_CONTROLS = frozenset(
    {
        "cross_tenant_login",
        "provider_contract",
        "cold_warm_order",
        "cache_isolation",
        "schema",
        "model_routing",
        "relationships",
        "static_controls",
        "time_series_controls",
        "permission_denial",
        "refresh_revocation_unlink",
        "provenance",
        "no_secrets",
        "latency",
        "no_fallback",
    }
)


class OntologyCloudObservations(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    provider: Literal["ontology"] = "ontology"
    state: Literal["passed"] = "passed"
    topology: Literal["cross_tenant"] = "cross_tenant"
    run_id: str = Field(alias="runId", pattern=r"^run_[A-Za-z0-9_-]{8,}$")
    deployment_sha256: str = Field(alias="deploymentSha256", pattern=r"^[a-f0-9]{64}$")
    provider_contract_sha256: str = Field(alias="providerContractSha256", pattern=r"^[a-f0-9]{64}$")
    fixture_sha256: str = Field(alias="fixtureSha256", pattern=r"^[a-f0-9]{64}$")
    tenant_hashes: tuple[str, str] = Field(alias="tenantHashes")
    tool_names: tuple[Literal["list_ontology_entity_types", "search_ontology"], ...] = Field(
        alias="toolNames",
        min_length=2,
        max_length=2,
    )
    cold_call_order: tuple[str, ...] = Field(alias="coldCallOrder")
    warm_call_order: tuple[str, ...] = Field(alias="warmCallOrder")
    entity_schema_sha256: str = Field(alias="entitySchemaSha256", pattern=r"^[a-f0-9]{64}$")
    relationship_sha256: str = Field(alias="relationshipSha256", pattern=r"^[a-f0-9]{64}$")
    static_controls_sha256: str = Field(alias="staticControlsSha256", pattern=r"^[a-f0-9]{64}$")
    time_series_controls_sha256: str = Field(alias="timeSeriesControlsSha256", pattern=r"^[a-f0-9]{64}$")
    denied_error_code: Literal["authorization_denied"] = Field(alias="deniedErrorCode")
    refresh_revocation_unlink: Literal["passed"] = Field(alias="refreshRevocationUnlink")
    model_profile: Literal["gpt-5.6-terra-medium-v1"] = Field(alias="modelProfile")
    served_model: Literal["gpt-5.6-terra"] = Field(alias="servedModel")
    served_snapshot: Literal["2026-07-09"] = Field(alias="servedSnapshot")
    prompt_sha256: str = Field(alias="promptSha256", pattern=r"^[a-f0-9]{64}$")
    request_options_sha256: str = Field(alias="requestOptionsSha256", pattern=r"^[a-f0-9]{64}$")
    provenance_reconciled: bool = Field(alias="provenanceReconciled")
    forbidden_markers_found: Literal[0] = Field(alias="forbiddenMarkersFound")
    fallback_paths_observed: tuple[str, ...] = Field(alias="fallbackPathsObserved", max_length=0)
    cold_latency_ms: tuple[int, ...] = Field(alias="coldLatencyMs", min_length=5, max_length=5)
    warm_latency_ms: tuple[int, ...] = Field(alias="warmLatencyMs", min_length=5, max_length=5)
    controls: dict[str, Literal["passed", "failed"]]
    observed_at: datetime = Field(alias="observedAt")

    @model_validator(mode="after")
    def validate_complete(self) -> OntologyCloudObservations:
        if frozenset(self.controls) != ONTOLOGY_ACCEPTANCE_CONTROLS or any(
            status != "passed" for status in self.controls.values()
        ):
            raise ValueError("ontology acceptance controls are incomplete or failed")
        if len(set(self.tenant_hashes)) != 2:
            raise ValueError("ontology acceptance requires two distinct tenant hashes")
        if self.tool_names != ("list_ontology_entity_types", "search_ontology"):
            raise ValueError("ontology provider tool contract does not match")
        if self.cold_call_order != (
            "initialize",
            "tools/list",
            "list_ontology_entity_types",
            "search_ontology",
        ):
            raise ValueError("cold ontology call order does not match")
        if self.warm_call_order != ("initialize", "tools/list", "search_ontology"):
            raise ValueError("warm ontology call order does not match")
        return self
