from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, cast

import pytest
from eda_worker.acceptance.documents import DOCUMENT_KINDS, DocumentAcceptanceObservations

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "run-document-observations.py"


def load_module() -> Any:
    specification = importlib.util.spec_from_file_location("run_document_observations", SCRIPT)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def session(**overrides: Any) -> dict[str, Any]:
    record = {
        "kind": "document",
        "allocation_ms": 139,
        "first_health_ms": 139,
        "import_ms": 146,
        "execution_ms": 4576,
        "validation_ms": 508,
        "stop_ms": 83,
        "peak_rss_bytes": 220979200,
        "oom": False,
        "cross_session_leakage": False,
    }
    record.update(overrides)
    return record


def test_counters_come_from_the_document_sessions_only() -> None:
    module = load_module()
    benchmark = {
        "sessions": [
            session(),
            session(peak_rss_bytes=298856448, execution_ms=9000),
            {"kind": "core", "peak_rss_bytes": 4 * 1024**3, "oom": True, "cross_session_leakage": True},
        ]
    }

    counters = cast(dict[str, Any], module._benchmark_counters(benchmark))

    assert counters["documentSessions"] == 2
    # The core session is the largest and the noisiest; letting it in would hide a document regression.
    assert counters["maxPeakMemoryRatio"] == pytest.approx(298856448 / (4 * 1024**3))
    assert counters["maxDurationSeconds"] == pytest.approx((139 + 139 + 146 + 9000 + 508 + 83) / 1000)
    assert counters["oomCount"] == 0
    assert counters["crossSessionLeakCount"] == 0


def test_a_benchmark_without_document_sessions_is_refused() -> None:
    module = load_module()

    with pytest.raises(module.ObservationFailure):
        module._benchmark_counters({"sessions": [{"kind": "core"}]})


def test_publication_reports_the_second_version() -> None:
    module = load_module()
    generations = [
        module.DocumentGeneration(artifact_sha256="a" * 64, report_sha256="b" * 64, preview_sha256="c" * 64),
        module.DocumentGeneration(artifact_sha256="d" * 64, report_sha256="e" * 64, preview_sha256="f" * 64),
    ]

    assert module._publish("pdf", generations) == 2


def test_every_scanned_surface_the_gate_demands_is_listed() -> None:
    module = load_module()
    observations = DocumentAcceptanceObservations.model_validate(
        {
            "schemaVersion": 1,
            "state": "passed",
            "runId": "run_0123456789ab",
            "deploymentId": "zftulkxrp2r7o",
            "contractSha256": "1" * 64,
            "workerImageDigest": "sha256:" + "2" * 64,
            "sandboxImageDigest": "sha256:" + "3" * 64,
            "lockSha256": "4" * 64,
            "commit": "5" * 40,
            "bundles": {kind: "6" * 64 for kind in DOCUMENT_KINDS},
            "termsFailureBuildCode": "terms_mismatch",
            "firstArtifactSha256": {kind: "7" * 64 for kind in DOCUMENT_KINDS},
            "secondArtifactSha256": {kind: "8" * 64 for kind in DOCUMENT_KINDS},
            "validationReportSha256": {kind: "9" * 64 for kind in DOCUMENT_KINDS},
            "previewSha256": {kind: "a" * 64 for kind in DOCUMENT_KINDS},
            "publishedVersions": dict.fromkeys(DOCUMENT_KINDS, 2),
            "benchmarkSha256": "b" * 64,
            "leakedSkillMarkers": 0,
            "scannedSurfaces": list(module.SURFACES),
            "tests": dict.fromkeys(("acquisition", "generation_validation", "no_leak", "concurrency"), "passed"),
            "observedAt": "2026-08-04T00:00:00+00:00",
            "documentSessions": len(DOCUMENT_KINDS),
            "maxPeakMemoryRatio": 0.07,
            "maxDurationSeconds": 5.6,
            "oomCount": 0,
            "crossSessionLeakCount": 0,
        }
    )

    assert set(observations.scanned_surfaces) == set(module.SURFACES)
