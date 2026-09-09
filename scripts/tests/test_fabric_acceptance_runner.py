from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts/run-fabric-acceptance.sh"
PREPARER = ROOT / "scripts/prepare-fabric-acceptance.py"
FINALIZER = ROOT / "scripts/finalize-fabric-acceptance.py"


def run(environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 -- fixed repository acceptance script.
        [str(RUNNER)],
        check=False,
        capture_output=True,
        text=True,
        cwd=ROOT,
        env=environment,
    )


def test_disabled_runner_exits_zero_with_exact_skip() -> None:
    environment = os.environ.copy()
    environment["FABRIC_ENABLED"] = "false"

    result = run(environment)

    assert result.returncode == 0
    assert result.stdout.strip() == "SKIP: Fabric intentionally disabled"


def test_enabled_runner_fails_closed_when_required_context_is_missing() -> None:
    environment = os.environ.copy()
    environment["FABRIC_ENABLED"] = "true"
    environment["FABRIC_PROVIDER"] = "semantic_model"
    for name in (
        "PRODUCT_AZURE_CONFIG_DIR",
        "FABRIC_ACCEPTANCE_JOB_ID",
        "FABRIC_ACCEPTANCE_FIXTURE",
    ):
        environment.pop(name, None)

    result = run(environment)

    assert result.returncode != 0
    assert "must be set" in result.stderr


def test_runner_and_crypto_scripts_preserve_acceptance_security_boundaries() -> None:
    runner = RUNNER.read_text(encoding="utf-8")
    preparer = PREPARER.read_text(encoding="utf-8")
    finalizer = FINALIZER.read_text(encoding="utf-8")

    assert "set -euo pipefail" in runner
    assert "pytest -m cloud tests/cloud/fabric" in runner
    assert "prepare-fabric-acceptance.py" in runner
    assert "finalize-fabric-acceptance.py" in runner
    assert '--name "$job_name"' in runner
    assert '--job-execution-name "$execution_name"' in runner
    assert "--job-name" not in runner
    assert "KeyWrapAlgorithm.rsa_oaep_256" in preparer
    assert ".unwrap_key(" not in preparer
    assert ".sign(" not in preparer
    assert "MatchConditions.IfNotModified" in finalizer
    assert "upsert_item" not in finalizer
    assert stat.S_IMODE(RUNNER.stat().st_mode) & stat.S_IXUSR
