from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import cast

import pytest

SENTINEL_FIELDS = (
    "prompt",
    "fixtureCell",
    "authCode",
    "token",
    "cookie",
    "sasSignature",
    "sessionId",
    "blobName",
    "generatedSource",
)


def test_telemetry_scan_is_correlation_scoped_and_fail_closed() -> None:
    source = Path(__file__).resolve().parents[2].joinpath("scripts", "deploy-smoke.sh").read_text(encoding="utf-8")
    assert "EDA_TELEMETRY_CORRELATION_ID" in source
    assert "test_telemetry_leaks.py" in source
    assert "pytest" in source
    assert "SKIP" not in source


def _require_cloud_inputs() -> tuple[str, str, dict[str, str]]:
    if not os.environ.get("EDA_APP_URL"):
        pytest.skip("set EDA_APP_URL to run telemetry leak checks")
    if shutil.which("az") is None:
        pytest.skip("Azure CLI is unavailable")
    account = subprocess.run(
        ["az", "account", "show", "--only-show-errors", "--output", "none"],  # noqa: S607 -- fixed Azure CLI prerequisite check.
        check=False,
        capture_output=True,
        encoding="utf-8",
    )
    if account.returncode != 0:
        pytest.skip("Azure credentials are unavailable")
    app_insights_id = os.environ.get("APP_INSIGHTS_ID")
    correlation_id = os.environ.get("EDA_TELEMETRY_CORRELATION_ID")
    sentinels = os.environ.get("EDA_TELEMETRY_SENTINELS_JSON")
    if not app_insights_id or not correlation_id or not sentinels:
        pytest.fail(
            "deployed telemetry checks require APP_INSIGHTS_ID, EDA_TELEMETRY_CORRELATION_ID, "
            "and EDA_TELEMETRY_SENTINELS_JSON"
        )
    value: object = json.loads(sentinels)
    assert isinstance(value, dict)
    sentinel_values = cast(dict[str, str], value)
    assert set(SENTINEL_FIELDS).issubset(sentinel_values)
    assert all(isinstance(sentinel, str) for sentinel in sentinel_values.values())
    return app_insights_id, correlation_id, sentinel_values


@pytest.mark.cloud
def test_correlation_has_expected_spans_without_sensitive_content() -> None:
    app_insights_id, correlation_id, sentinels = _require_cloud_inputs()
    query = (
        "union traces, requests, dependencies "
        f"| where operation_Id == '{correlation_id}' "
        "| project message, name, customDimensions"
    )
    result = subprocess.run(  # noqa: S603 -- fixed Azure CLI query.
        [  # noqa: S607 -- Azure CLI is explicit.
            "az",
            "monitor",
            "app-insights",
            "query",
            "--app",
            app_insights_id,
            "--analytics-query",
            query,
            "--only-show-errors",
            "--output",
            "json",
        ],
        check=False,
        capture_output=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
    payload: object = json.loads(result.stdout)
    serialized = json.dumps(payload).lower()
    for span in ("api", "dts", "model", "sandbox", "artifact"):
        assert span in serialized
    for name, sentinel in sentinels.items():
        assert isinstance(name, str) and isinstance(sentinel, str) and sentinel
        assert sentinel.lower() not in serialized, f"telemetry leaked sentinel: {name}"
