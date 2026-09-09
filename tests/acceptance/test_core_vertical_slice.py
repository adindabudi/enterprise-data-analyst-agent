from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REQUIRED_ARTIFACTS = {
    "analysis.xlsx",
    "report.html",
    "report-desktop.png",
    "report-mobile.png",
    "revenue-chart.svg",
    "revenue-chart.png",
    "revenue-flow.mmd",
    "revenue-flow.svg",
    "revenue-flow.png",
    "task-manifest.json",
    "reproducibility.zip",
}


def test_core_vertical_slice_is_deterministic_and_fully_validated(tmp_path: Path) -> None:
    output = tmp_path / "core-acceptance-manifest.json"
    workspace = tmp_path / "workspace"

    subprocess.run(  # noqa: S603
        [
            sys.executable,
            "scripts/run-core-acceptance.py",
            "--workspace",
            str(workspace),
            "--output",
            str(output),
        ],
        check=True,
    )

    manifest = json.loads(output.read_text(encoding="utf-8"))
    assert manifest["status"] == "passed"
    assert manifest["expectedTotal"] == "1250000.50"
    assert {artifact["name"] for artifact in manifest["artifacts"]} == REQUIRED_ARTIFACTS
    assert all(artifact["ready"] for artifact in manifest["artifacts"])
    assert all(len(artifact["sha256"]) == 64 for artifact in manifest["artifacts"])
    assert all(manifest["checks"].values())
    assert set(manifest["checks"]) >= {
        "formulaCacheMatchesControl",
        "htmlHasNoNetworkReferences",
        "desktopAndMobileScreenshotsValid",
        "chartDimensionsBounded",
        "mermaidSourceAndRendersValid",
        "manifestAndBundleValid",
        "importantClaimsResolve",
        "runtimeEvidenceComplete",
        "publicationIsIdempotent",
        "noPreReadyDownload",
        "steeringAppliedOnce",
        "reconnectDidNotDuplicate",
    }
