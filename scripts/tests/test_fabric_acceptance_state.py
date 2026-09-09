from __future__ import annotations

from datetime import UTC, datetime

import pytest
from eda_worker.acceptance.fabric import FabricAcceptanceResult
from eda_worker.fabric.readiness import FabricFeatureRecord

from scripts.fabric_acceptance_state import (
    REQUIRED_TESTS,
    FabricAcceptanceManifest,
    acceptance_manifest_sha256,
    validate_promotion,
)


def feature(**updates: object) -> FabricFeatureRecord:
    values: dict[str, object] = {
        "state": "configured",
        "deploymentId": "deployment-20260724",
        "providerContractSha256": "a" * 64,
        "modelProfile": "gpt-5.6-terra-medium-v1",
        "modelDeployment": "gpt-5.6-terra",
        "servedModel": "gpt-5.6-terra",
        "servedSnapshot": "2026-07-09",
        "promptVersion": "gpt-5.6-terra-v1",
        "promptSha256": "b" * 64,
        "requestOptionsSha256": "c" * 64,
        "verifiedAt": datetime.now(UTC),
    }
    values.update(updates)
    return FabricFeatureRecord.model_validate(values)


def result(**updates: object) -> FabricAcceptanceResult:
    values: dict[str, object] = {
        "runId": "run_01HZZZZZZZZZZZZZZZZZZZZZZZ",
        "deploymentId": "deployment-20260724",
        "providerContractSha256": "a" * 64,
        "observations": [
            {"label": "rls_a", "status": "passed", "resultSha256": "d" * 64},
            {"label": "rls_b", "status": "passed", "resultSha256": "e" * 64},
            {"label": "no_build", "status": "passed", "errorCode": "build_permission_denied"},
        ],
        "controls": {
            "rls_a_control": "passed",
            "rls_b_control": "passed",
            "build_denial": "passed",
            "rls_separation": "passed",
            "all_fixtures": "passed",
        },
    }
    values.update(updates)
    return FabricAcceptanceResult.model_validate(values)


def manifest(**updates: object) -> FabricAcceptanceManifest:
    values: dict[str, object] = {
        "runId": "run_01HZZZZZZZZZZZZZZZZZZZZZZZ",
        "deploymentId": "deployment-20260724",
        "providerContractSha256": "a" * 64,
        "modelProfile": "gpt-5.6-terra-medium-v1",
        "servedModel": "gpt-5.6-terra",
        "servedSnapshot": "2026-07-09",
        "promptVersion": "gpt-5.6-terra-v1",
        "promptSha256": "b" * 64,
        "requestOptionsSha256": "c" * 64,
        "tenantHashes": ["d" * 64, "e" * 64],
        "tests": {name: "passed" for name in REQUIRED_TESTS},
        "startedAt": "2026-07-24T00:00:00Z",
        "completedAt": "2026-07-24T00:10:00Z",
    }
    values.update(updates)
    return FabricAcceptanceManifest.model_validate(values)


def promote(
    *,
    active_principal_id: str = "11111111-1111-1111-1111-111111111111",
    expected_principal_id: str = "11111111-1111-1111-1111-111111111111",
    feature_record: FabricFeatureRecord | None = None,
    acceptance_result: FabricAcceptanceResult | None = None,
    acceptance_manifest: FabricAcceptanceManifest | None = None,
) -> FabricFeatureRecord:
    return validate_promotion(
        active_principal_id=active_principal_id,
        expected_principal_id=expected_principal_id,
        feature=feature_record or feature(),
        result=acceptance_result or result(),
        manifest=acceptance_manifest or manifest(),
    )


def test_valid_transition_is_hash_bound_and_ready() -> None:
    evidence = manifest()

    promoted = promote(acceptance_manifest=evidence)

    assert promoted.state == "ready"
    assert promoted.acceptance_evidence_sha256 == acceptance_manifest_sha256(evidence)


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("deploymentId", "other-deployment", "deployment"),
        ("providerContractSha256", "f" * 64, "provider contract"),
        ("modelProfile", "other-profile", "model profile"),
        ("servedModel", "other-model", "served model"),
        ("servedSnapshot", "other-snapshot", "served snapshot"),
        ("promptVersion", "other-prompt", "prompt version"),
        ("promptSha256", "f" * 64, "prompt hash"),
        ("requestOptionsSha256", "f" * 64, "request options"),
    ],
)
def test_model_and_deployment_mismatch_never_promotes(field: str, value: object, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        promote(acceptance_manifest=manifest(**{field: value}))


def test_wrong_principal_reused_state_and_failed_job_never_promote() -> None:
    with pytest.raises(ValueError, match="principal"):
        promote(active_principal_id="22222222-2222-2222-2222-222222222222")
    with pytest.raises(ValueError, match="not promotable"):
        promote(feature_record=feature(state="ready", acceptanceEvidenceSha256="f" * 64))
    failed_controls = result().model_dump(mode="json", by_alias=True)
    failed_controls["controls"]["build_denial"] = "failed"
    with pytest.raises(ValueError, match="did not pass"):
        promote(acceptance_result=FabricAcceptanceResult.model_validate(failed_controls))


def test_manifest_rejects_missing_extra_or_failed_tests_and_same_tenant_hash() -> None:
    missing = {name: "passed" for name in REQUIRED_TESTS if name != "no_secrets"}
    with pytest.raises(ValueError, match="incomplete"):
        manifest(tests=missing)
    extra = {name: "passed" for name in REQUIRED_TESTS}
    extra["invented"] = "passed"
    with pytest.raises(ValueError, match="incomplete"):
        manifest(tests=extra)
    failed = {name: "passed" for name in REQUIRED_TESTS}
    failed["rls"] = "failed"
    with pytest.raises(ValueError, match="failed"):
        manifest(tests=failed)
    with pytest.raises(ValueError, match="distinct tenant"):
        manifest(tenantHashes=["d" * 64, "d" * 64])
