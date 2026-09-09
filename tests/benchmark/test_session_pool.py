from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.cloud
RUN_LIVE = os.environ.get("EDA_RUN_SANDBOX_BENCHMARK") == "1"


@pytest.mark.skipif(not RUN_LIVE, reason="set EDA_RUN_SANDBOX_BENCHMARK=1 to run the cloud isolation benchmark")
def test_session_pool_isolation_and_single_image_profile(tmp_path: Path) -> None:
    output = tmp_path / "sandbox-benchmark.json"
    endpoint = os.environ["EDA_SESSION_POOL_MANAGEMENT_ENDPOINT"]

    subprocess.run(  # noqa: S603
        [
            sys.executable,
            "scripts/run-sandbox-benchmark.py",
            "--include-documents",
            "--output",
            str(output),
        ],
        check=True,
    )

    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "passed"
    assert report["concurrency"] == {"core": 10, "document": 3}
    assert report["summary"]["coreSessions"] == 10
    assert report["summary"]["documentSessions"] == 3
    assert report["summary"]["maxPeakRssBytes"] < 3_435_973_836
    assert report["summary"]["stopP95Ms"] <= 15_000
    assert len(report["sessions"]) == 13
    assert all(session["completed"] for session in report["sessions"])
    assert all(
        not any(
            session[field]
            for field in (
                "oom",
                "processEscape",
                "fileEscape",
                "networkSuccess",
                "crossSessionLeakage",
                "lingeringAfterStop",
            )
        )
        for session in report["sessions"]
    )

    artifact = output.read_text(encoding="utf-8")
    assert endpoint not in artifact
    assert "identifier" not in artifact.lower()
    assert "sentinel" not in artifact.lower()
