from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from eda_worker.acceptance.documents import (
    DocumentAcceptanceObservations,
    DocumentImageContract,
    document_contract_sha256,
)
from eda_worker.documents.readiness import DocumentFeatureRecord

from scripts.document_acceptance_state import validate_document_promotion

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts" / "run-document-acceptance.sh"
LOCK = ROOT / "skills.lock.json"


def write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def document_report(**overrides: object) -> dict[str, object]:
    report: dict[str, object] = {
        "schemaVersion": 1,
        "state": "ready",
        "bundles": {"docx": "a" * 64, "pdf": "b" * 64, "pptx": "c" * 64, "xlsx": "d" * 64},
    }
    report.update(overrides)
    return report


def run_runner(environment: dict[str, str], *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 -- invokes the fixed repository runner with test-controlled paths.
        [str(RUNNER), *arguments],
        check=False,
        capture_output=True,
        text=True,
        cwd=ROOT,
        env=environment,
    )


def enabled_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "DOCUMENTS_ENABLED": "true",
            "EDA_DOCUMENT_TERMS_ACCEPTED": hashlib.sha256(LOCK.read_bytes()).hexdigest(),
        }
    )
    return environment


def test_disabled_document_pack_skips_with_exact_message() -> None:
    environment = os.environ.copy()
    environment["DOCUMENTS_ENABLED"] = "false"

    result = run_runner(environment)

    assert result.returncode == 0
    assert result.stdout == "SKIP: Document Pack intentionally disabled\n"
    assert result.stderr == ""


def test_enabled_document_pack_rejects_invalid_acceptance_report(tmp_path: Path) -> None:
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(document_report(state="configured")), encoding="utf-8")
    verifier = tmp_path / "verify-readiness"
    write_executable(verifier, "#!/bin/sh\nexit 0\n")

    result = run_runner(
        enabled_environment(),
        "--report",
        str(report_path),
        "--verify-readiness",
        str(verifier),
    )

    assert result.returncode != 0
    assert "FAIL:" in result.stderr


def test_enabled_document_pack_composes_real_deployed_gate() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    finalizer = (ROOT / "scripts/finalize-document-acceptance.py").read_text(encoding="utf-8")

    assert "tests/acceptance/test_document_pack.py" in source
    assert "pytest -m cloud" in source and "tests/cloud/documents" in source
    assert "tests/benchmark/test_document_concurrency.py" in source
    assert "benchmark_sha256" in source
    assert "publish-document-contract.py" in source
    assert "finalize-document-acceptance.py" in source
    assert "restart_active_revision" in source
    assert "documents=ready" in source
    assert "MatchConditions.IfNotModified" in finalizer
    assert "mode-0600 regular file" in finalizer
    assert "upsert_item" not in finalizer


def test_enabled_document_pack_does_not_echo_rejected_skill_content(tmp_path: Path) -> None:
    sentinel = "SOURCE_AVAILABLE_SKILL_CONTENT_MUST_NOT_LEAK"
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(document_report(skillContent=sentinel)), encoding="utf-8")
    verifier = tmp_path / "verify-readiness"
    output_path = tmp_path / "document-acceptance.json"
    write_executable(verifier, "#!/bin/sh\nexit 0\n")

    result = run_runner(
        enabled_environment(),
        "--report",
        str(report_path),
        "--verify-readiness",
        str(verifier),
        "--output",
        str(output_path),
    )

    assert result.returncode != 0
    assert sentinel not in result.stdout + result.stderr
    assert not output_path.exists()


def image_contract(**updates: object) -> DocumentImageContract:
    value: dict[str, object] = {
        "schemaVersion": 1,
        "deploymentId": "deployment-1",
        "workerImageDigest": f"sha256:{'a' * 64}",
        "sandboxImageDigest": f"sha256:{'9' * 64}",
        "lockSha256": "b" * 64,
        "commit": "c" * 40,
        "bundles": {"docx": "d" * 64, "pdf": "e" * 64, "pptx": "f" * 64, "xlsx": "1" * 64},
    }
    value.update(updates)
    return DocumentImageContract.model_validate(value)


def feature(contract: DocumentImageContract, **updates: object) -> DocumentFeatureRecord:
    value: dict[str, object] = {
        "state": "configured",
        "deploymentId": contract.deployment_id,
        "contractSha256": document_contract_sha256(contract),
        "workerImageDigest": contract.worker_image_digest,
        "sandboxImageDigest": contract.sandbox_image_digest,
        "lockSha256": contract.lock_sha256,
        "commit": contract.commit,
        "bundles": contract.bundles,
        "verifiedAt": datetime(2026, 7, 24, tzinfo=UTC),
    }
    value.update(updates)
    return DocumentFeatureRecord.model_validate(value)


def observations(contract: DocumentImageContract, **updates: object) -> DocumentAcceptanceObservations:
    value: dict[str, object] = {
        "schemaVersion": 1,
        "state": "passed",
        "runId": "run_document123",
        "deploymentId": contract.deployment_id,
        "contractSha256": document_contract_sha256(contract),
        "workerImageDigest": contract.worker_image_digest,
        "sandboxImageDigest": contract.sandbox_image_digest,
        "lockSha256": contract.lock_sha256,
        "commit": contract.commit,
        "bundles": contract.bundles,
        "termsFailureBuildCode": "terms_mismatch",
        "firstArtifactSha256": {"docx": "1" * 64, "pdf": "2" * 64, "pptx": "3" * 64, "xlsx": "a1" * 32},
        "secondArtifactSha256": {"docx": "4" * 64, "pdf": "5" * 64, "pptx": "6" * 64, "xlsx": "7" * 64},
        "validationReportSha256": {"docx": "7" * 64, "pdf": "8" * 64, "pptx": "9" * 64, "xlsx": "0" * 64},
        "previewSha256": {"docx": "a" * 64, "pdf": "b" * 64, "pptx": "c" * 64, "xlsx": "d" * 64},
        "publishedVersions": {"docx": 2, "pdf": 2, "pptx": 2, "xlsx": 2},
        "benchmarkSha256": "d" * 64,
        "documentSessions": 4,
        "maxPeakMemoryRatio": 0.7,
        "maxDurationSeconds": 200,
        "oomCount": 0,
        "crossSessionLeakCount": 0,
        "leakedSkillMarkers": 0,
        "scannedSurfaces": ["application_logs", "app_insights", "cosmos", "blob_manifests"],
        "tests": {
            "acquisition": "passed",
            "generation_validation": "passed",
            "no_leak": "passed",
            "concurrency": "passed",
        },
        "observedAt": "2026-07-24T00:10:00Z",
    }
    value.update(updates)
    return DocumentAcceptanceObservations.model_validate(value)


def test_complete_matching_cloud_evidence_promotes_and_is_idempotent() -> None:
    contract = image_contract()
    evidence = observations(contract)

    promoted = validate_document_promotion(feature=feature(contract), contract=contract, observations=evidence)
    repeated = validate_document_promotion(feature=promoted, contract=contract, observations=evidence)

    assert promoted.state == "ready"
    assert promoted.evidence_sha256 is not None
    assert repeated == promoted


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("deploymentId", "other", "deployment"),
        ("workerImageDigest", f"sha256:{'0' * 64}", "worker image"),
        ("sandboxImageDigest", f"sha256:{'0' * 64}", "sandbox image"),
        ("lockSha256", "0" * 64, "lock"),
        ("commit", "0" * 40, "commit"),
        (
            "bundles",
            {"docx": "0" * 64, "pdf": "e" * 64, "pptx": "f" * 64, "xlsx": "1" * 64},
            "bundles",
        ),
    ],
)
def test_any_cloud_observation_drift_rejects_promotion(field: str, value: object, match: str) -> None:
    contract = image_contract()
    with pytest.raises(ValueError, match=match):
        validate_document_promotion(
            feature=feature(contract),
            contract=contract,
            observations=observations(contract, **{field: value}),
        )


def test_failed_or_differently_ready_feature_cannot_promote() -> None:
    contract = image_contract()
    evidence = observations(contract)
    with pytest.raises(ValueError, match="promotable"):
        validate_document_promotion(feature=feature(contract, state="failed"), contract=contract, observations=evidence)
    with pytest.raises(ValueError, match="different evidence"):
        validate_document_promotion(
            feature=feature(contract, state="ready", evidenceSha256="0" * 64),
            contract=contract,
            observations=evidence,
        )
