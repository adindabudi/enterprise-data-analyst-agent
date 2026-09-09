from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from eda_worker.fabric.contracts import (
    FabricPrincipal,
    FabricQueryOperation,
    FabricQueryPurpose,
    FabricQueryResult,
)
from eda_worker.fabric.readiness import FabricFeatureRecord

FABRIC_ACCEPTANCE_TESTS = frozenset(
    {
        "provider_contract",
        "cross_tenant_login",
        "runtime_allowlist",
        "silent_refresh",
        "revoked_refresh",
        "unlink_relink",
        "rls",
        "build_permission",
        "harness_forced_first",
        "harness_automatic",
        "provenance",
        "no_secrets",
        "no_fallback",
    }
)


class AcceptanceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class FabricAcceptanceFixture(AcceptanceModel):
    label: Literal["rls_a", "rls_b", "no_build"]
    principal: FabricPrincipal
    task_id: str = Field(alias="taskId", pattern=r"^task_[A-Za-z0-9_-]{8,}$")
    invocation_id: str = Field(alias="invocationId", pattern=r"^[A-Za-z0-9_-]{8,128}$")
    semantic_model: str = Field(alias="semanticModel", pattern=r"^[a-z][a-z0-9-]{1,39}$")
    question: str = Field(min_length=1, max_length=4_000)
    expected_result_sha256: str | None = Field(
        default=None,
        alias="expectedResultSha256",
        pattern=r"^[a-f0-9]{64}$",
    )

    @model_validator(mode="after")
    def validate_expected_result(self) -> FabricAcceptanceFixture:
        if self.label == "no_build" and self.expected_result_sha256 is not None:
            raise ValueError("no-Build fixture cannot declare an expected result")
        if self.label != "no_build" and self.expected_result_sha256 is None:
            raise ValueError("RLS fixture requires an expected result hash")
        return self


class FabricAcceptanceInput(AcceptanceModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    run_id: str = Field(alias="runId", pattern=r"^run_[A-Za-z0-9_-]{8,}$")
    deployment_id: str = Field(alias="deploymentId", min_length=1, max_length=128)
    provider_contract_sha256: str = Field(alias="providerContractSha256", pattern=r"^[a-f0-9]{64}$")
    fixtures: tuple[FabricAcceptanceFixture, ...] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def validate_fixture_set(self) -> FabricAcceptanceInput:
        by_label = {fixture.label: fixture for fixture in self.fixtures}
        if set(by_label) != {"rls_a", "rls_b", "no_build"}:
            raise ValueError("acceptance input requires exactly two RLS fixtures and one no-Build fixture")
        if by_label["rls_a"].expected_result_sha256 == by_label["rls_b"].expected_result_sha256:
            raise ValueError("RLS fixtures must have distinct expected result hashes")
        return self


class FabricAcceptanceObservation(AcceptanceModel):
    label: Literal["rls_a", "rls_b", "no_build"]
    status: Literal["passed", "failed"]
    result_sha256: str | None = Field(default=None, alias="resultSha256", pattern=r"^[a-f0-9]{64}$")
    error_code: str | None = Field(default=None, alias="errorCode", max_length=80)


class FabricAcceptanceResult(AcceptanceModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    provider: Literal["semantic_model"] = "semantic_model"
    run_id: str = Field(alias="runId", pattern=r"^run_[A-Za-z0-9_-]{8,}$")
    deployment_id: str = Field(alias="deploymentId", min_length=1, max_length=128)
    provider_contract_sha256: str = Field(alias="providerContractSha256", pattern=r"^[a-f0-9]{64}$")
    observations: tuple[FabricAcceptanceObservation, ...] = Field(min_length=3, max_length=3)
    controls: dict[str, Literal["passed", "failed"]]


class AcceptanceGateway(Protocol):
    async def query(
        self,
        task_id: str,
        invocation_id: str,
        principal: FabricPrincipal,
        operation: FabricQueryOperation,
    ) -> FabricQueryResult: ...


async def run_fabric_acceptance(
    acceptance_input: FabricAcceptanceInput,
    *,
    feature: FabricFeatureRecord,
    gateway: AcceptanceGateway,
    acceptance_mode: bool,
) -> FabricAcceptanceResult:
    if not acceptance_mode:
        raise ValueError("Fabric acceptance mode is disabled")
    if feature.state != "configured" or feature.acceptance_evidence_sha256 is not None:
        raise ValueError("Fabric acceptance requires configured, unpromoted feature state")
    if feature.deployment_id != acceptance_input.deployment_id:
        raise ValueError("Fabric acceptance deployment does not match")
    if feature.provider_contract_sha256 != acceptance_input.provider_contract_sha256:
        raise ValueError("Fabric acceptance provider contract does not match")

    observations: list[FabricAcceptanceObservation] = []
    controls: dict[str, Literal["passed", "failed"]] = {}
    for fixture in sorted(acceptance_input.fixtures, key=lambda value: value.label):
        operation = FabricQueryOperation(
            semantic_model=fixture.semantic_model,
            purpose=FabricQueryPurpose.CONTROL_TOTAL,
            question=fixture.question,
        )
        result = await gateway.query(
            fixture.task_id,
            fixture.invocation_id,
            fixture.principal,
            operation,
        )
        if fixture.label == "no_build":
            passed = result.status == "authorization_error" and not result.retryable and not result.artifact_refs
            observations.append(
                FabricAcceptanceObservation(
                    label=fixture.label,
                    status="passed" if passed else "failed",
                    errorCode=result.error_code,
                )
            )
            controls["build_denial"] = "passed" if passed else "failed"
            continue

        result_hash = result.artifact_refs[0].sha256 if len(result.artifact_refs) == 1 else None
        passed = result.status == "ok" and result_hash == fixture.expected_result_sha256
        observations.append(
            FabricAcceptanceObservation(
                label=fixture.label,
                status="passed" if passed else "failed",
                resultSha256=result_hash,
                errorCode=result.error_code,
            )
        )
        controls[f"{fixture.label}_control"] = "passed" if passed else "failed"

    observed_hashes = {
        observation.result_sha256
        for observation in observations
        if observation.label in {"rls_a", "rls_b"} and observation.result_sha256 is not None
    }
    controls["rls_separation"] = "passed" if len(observed_hashes) == 2 else "failed"
    controls["all_fixtures"] = "passed" if all(value == "passed" for value in controls.values()) else "failed"
    return FabricAcceptanceResult(
        runId=acceptance_input.run_id,
        deploymentId=acceptance_input.deployment_id,
        providerContractSha256=acceptance_input.provider_contract_sha256,
        observations=tuple(observations),
        controls=controls,
    )


def acceptance_passed(result: FabricAcceptanceResult) -> bool:
    required = {"rls_a_control", "rls_b_control", "build_denial", "rls_separation", "all_fixtures"}
    return set(result.controls) == required and all(value == "passed" for value in result.controls.values())


def sanitized_result_document(result: FabricAcceptanceResult) -> Mapping[str, object]:
    if not acceptance_passed(result):
        raise ValueError("Fabric acceptance controls did not all pass")
    return {
        "id": f"fabric-acceptance-result:{result.run_id}",
        "recordType": "fabricAcceptanceResult",
        **result.model_dump(mode="json", by_alias=True),
    }


def acceptance_input_aad(
    *,
    record_id: str,
    deployment_id: str,
    provider_contract_sha256: str,
) -> bytes:
    return json.dumps(
        {
            "schemaVersion": 1,
            "recordType": "fabricAcceptanceInput",
            "recordId": record_id,
            "deploymentId": deployment_id,
            "providerContractSha256": provider_contract_sha256,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
