from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SMOKE = ROOT / "scripts" / "run-fabric-ontology-smoke.sh"
ACCEPTANCE = ROOT / "scripts" / "run-fabric-ontology-acceptance.sh"
FINALIZE = ROOT / "scripts" / "finalize-fabric-ontology-acceptance.py"


def write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def sha256(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def acceptance_report(**overrides: object) -> dict[str, object]:
    report: dict[str, object] = {
        "schemaVersion": 1,
        "provider": "ontology",
        "state": "configured",
        "topology": "cross_tenant",
        "runId": "run_01HZZZZZZZZZZZZZZZZZZZZZZZ",
        "providerContractDigest": "a" * 64,
        "authContractDigest": "b" * 64,
        "deploymentDigest": "c" * 64,
        "fixtureDigest": "d" * 64,
        "controls": {"contract": "passed", "schema": "passed", "permissions": "passed"},
    }
    report.update(overrides)
    return report


def smoke_report(**overrides: object) -> dict[str, object]:
    report = acceptance_report(state="smoke_passed", topology="same_tenant")
    report.update(overrides)
    return report


def write_report(path: Path, report: dict[str, object]) -> Path:
    path.write_text(json.dumps(report), encoding="utf-8")
    return path


def run_script(script: Path, environment: dict[str, str], *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 -- invokes fixed repository scripts with test-controlled reports.
        [str(script), *arguments],
        check=False,
        capture_output=True,
        text=True,
        cwd=ROOT,
        env=environment,
    )


def test_smoke_writes_only_hash_only_same_tenant_state(tmp_path: Path) -> None:
    report = smoke_report()
    report_path = write_report(tmp_path / "smoke-report.json", report)
    output_path = tmp_path / "smoke-evidence.json"

    result = run_script(SMOKE, os.environ.copy(), "--report", str(report_path), "--output", str(output_path))

    assert result.returncode == 0, result.stderr
    evidence = json.loads(output_path.read_text(encoding="utf-8"))
    assert evidence == {
        "provider": "ontology",
        "reportSha256": sha256(report),
        "state": "smoke_passed",
        "topology": "same_tenant",
    }
    assert "run_" not in output_path.read_text(encoding="utf-8")


@pytest.mark.parametrize("argument", ["--promote", "--acceptance-principal-id", "--acceptance-principal-secret"])
def test_smoke_refuses_promotion_and_acceptance_principal_arguments(argument: str, tmp_path: Path) -> None:
    result = run_script(SMOKE, os.environ.copy(), argument, "value")

    assert result.returncode != 0
    assert not (tmp_path / "smoke-evidence.json").exists()


def test_acceptance_requires_cross_tenant_configured_ontology_report_and_invokes_worker(tmp_path: Path) -> None:
    report = acceptance_report()
    report_path = write_report(tmp_path / "acceptance-report.json", report)
    evidence_path = tmp_path / "worker-evidence.json"
    output_path = tmp_path / "acceptance-evidence.json"
    bin_directory = tmp_path / "bin"
    bin_directory.mkdir()
    invocation_path = tmp_path / "worker-invocation.txt"
    write_executable(
        bin_directory / "eda-worker",
        '#!/bin/sh\nprintf "%s\\n" "$*" > "$EDA_WORKER_INVOCATION"\n',
    )
    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{bin_directory}:{environment['PATH']}",
            "EDA_WORKER_INVOCATION": str(invocation_path),
        }
    )

    result = run_script(
        ACCEPTANCE,
        environment,
        "--report",
        str(report_path),
        "--evidence",
        str(evidence_path),
        "--output",
        str(output_path),
    )

    assert result.returncode == 0, result.stderr
    assert invocation_path.read_text(encoding="utf-8").strip() == (
        f"accept-fabric --provider ontology --evidence {evidence_path}"
    )
    worker_evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert worker_evidence == {
        "authContractDigest": "b" * 64,
        "provider": "ontology",
        "providerContractDigest": "a" * 64,
        "runId": report["runId"],
        "state": "configured",
        "topology": "cross_tenant",
    }
    assert json.loads(output_path.read_text(encoding="utf-8")) == {
        "evidenceSha256": sha256(worker_evidence),
        "provider": "ontology",
        "reportSha256": sha256(report),
        "state": "configured",
        "topology": "cross_tenant",
    }


@pytest.mark.parametrize(
    "overrides",
    [
        {"topology": "same_tenant"},
        {"state": "ready"},
        {"provider": "semantic_model"},
        {"providerContractDigest": "not-a-digest"},
        {"controls": {"contract": "failed", "schema": "passed", "permissions": "passed"}},
    ],
)
def test_acceptance_rejects_invalid_readiness_report(overrides: dict[str, object], tmp_path: Path) -> None:
    report_path = write_report(tmp_path / "report.json", acceptance_report(**overrides))
    result = run_script(
        ACCEPTANCE,
        os.environ.copy(),
        "--report",
        str(report_path),
        "--evidence",
        str(tmp_path / "evidence.json"),
        "--output",
        str(tmp_path / "output.json"),
    )

    assert result.returncode != 0


@pytest.mark.parametrize(
    "overrides",
    [
        {"topology": "same_tenant"},
        {"state": "ready"},
        {"provider": "semantic_model"},
        {"authContractDigest": "not-a-digest"},
        {"providerContractDigest": None},
    ],
)
def test_finalizer_rejects_nonpromotable_report(overrides: dict[str, object], tmp_path: Path) -> None:
    report_path = write_report(tmp_path / "report.json", acceptance_report(**overrides))
    output_path = tmp_path / "prepared.json"

    result = subprocess.run(  # noqa: S603 -- invokes the fixed repository finalizer with a test report.
        [sys.executable, str(FINALIZE), "--report", str(report_path), "--output", str(output_path)],
        check=False,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )

    assert result.returncode != 0
    assert not output_path.exists()


def test_finalizer_prepares_hash_only_transition_without_network_write(tmp_path: Path) -> None:
    report = acceptance_report()
    report_path = write_report(tmp_path / "report.json", report)
    output_path = tmp_path / "prepared.json"

    result = subprocess.run(  # noqa: S603 -- invokes the fixed repository finalizer with a test report.
        [sys.executable, str(FINALIZE), "--report", str(report_path), "--output", str(output_path)],
        check=False,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(output_path.read_text(encoding="utf-8")) == {
        "fromState": "configured",
        "provider": "ontology",
        "reportSha256": sha256(report),
        "transition": "readiness_finalization_pending",
    }
    content = FINALIZE.read_text(encoding="utf-8")
    for network_marker in ("requests", "httpx", "azure.cosmos", "urllib", "socket"):
        assert network_marker not in content
