from __future__ import annotations

from datetime import UTC, datetime

import pytest
from eda_worker.acceptance.documents import (
    DocumentAcceptanceObservations,
    DocumentImageContract,
    document_contract_sha256,
    document_evidence_sha256,
)
from pydantic import ValidationError


def observation(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "schemaVersion": 1,
        "state": "passed",
        "runId": "run_document123",
        "deploymentId": "deployment-1",
        "contractSha256": "9" * 64,
        "workerImageDigest": f"sha256:{'a' * 64}",
        "sandboxImageDigest": f"sha256:{'e' * 64}",
        "lockSha256": "b" * 64,
        "commit": "c" * 40,
        "bundles": {"docx": "d" * 64, "pdf": "e" * 64, "pptx": "f" * 64, "xlsx": "0" * 64},
        "termsFailureBuildCode": "terms_mismatch",
        "firstArtifactSha256": {"docx": "1" * 64, "pdf": "2" * 64, "pptx": "3" * 64, "xlsx": "a1" * 32},
        "secondArtifactSha256": {"docx": "4" * 64, "pdf": "5" * 64, "pptx": "6" * 64, "xlsx": "b2" * 32},
        "validationReportSha256": {"docx": "7" * 64, "pdf": "8" * 64, "pptx": "9" * 64, "xlsx": "c3" * 32},
        "previewSha256": {"docx": "a" * 64, "pdf": "b" * 64, "pptx": "c" * 64, "xlsx": "d4" * 32},
        "publishedVersions": {"docx": 2, "pdf": 2, "pptx": 2, "xlsx": 2},
        "benchmarkSha256": "d" * 64,
        "documentSessions": 4,
        "maxPeakMemoryRatio": 0.72,
        "maxDurationSeconds": 240.0,
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
        "observedAt": datetime(2026, 7, 24, tzinfo=UTC).isoformat(),
    }
    value.update(overrides)
    return value


def test_acceptance_observation_is_complete_and_hashable() -> None:
    parsed = DocumentAcceptanceObservations.model_validate(observation())

    assert len(document_evidence_sha256(parsed)) == 64


def test_image_contract_is_complete_and_hashable() -> None:
    contract = DocumentImageContract.model_validate(
        {
            "schemaVersion": 1,
            "deploymentId": "deployment-1",
            "workerImageDigest": f"sha256:{'a' * 64}",
            "sandboxImageDigest": f"sha256:{'e' * 64}",
            "lockSha256": "b" * 64,
            "commit": "c" * 40,
            "bundles": {"docx": "d" * 64, "pdf": "e" * 64, "pptx": "f" * 64, "xlsx": "0" * 64},
        }
    )

    assert len(document_contract_sha256(contract)) == 64
    with pytest.raises(ValidationError):
        DocumentImageContract.model_validate({**contract.model_dump(by_alias=True), "bundles": {"docx": "d" * 64}})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("maxPeakMemoryRatio", 0.8),
        ("maxDurationSeconds", 300.1),
        ("crossSessionLeakCount", 1),
        ("leakedSkillMarkers", 1),
        ("tests", {"acquisition": "passed"}),
        ("secondArtifactSha256", {"docx": "1" * 64, "pdf": "5" * 64, "pptx": "6" * 64, "xlsx": "b2" * 32}),
        ("scannedSurfaces", ["application_logs"]),
    ],
)
def test_acceptance_observation_rejects_unready_evidence(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        DocumentAcceptanceObservations.model_validate(observation(**{field: value}))
