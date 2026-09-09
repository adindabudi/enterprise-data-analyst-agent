from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ROUTER = ROOT / "scripts/run-fabric-provider-hook.sh"


def executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def harness(tmp_path: Path, *, fail_name: str | None = None) -> tuple[Path, dict[str, str], Path]:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copy2(ROUTER, scripts / ROUTER.name)
    log = tmp_path / "calls.log"
    for name in (
        "doctor-fabric.sh",
        "doctor-fabric-ontology.sh",
        "run-fabric-acceptance.sh",
        "run-fabric-ontology-acceptance.sh",
    ):
        exit_code = 7 if name == fail_name else 0
        executable(
            scripts / name,
            f'#!/bin/sh\nprintf "%s %s\\n" "{name}" "$*" >> "$HOOK_LOG"\nexit {exit_code}\n',
        )
    bin_path = tmp_path / "bin"
    bin_path.mkdir()
    uv_exit = 7 if fail_name == "uv" else 0
    executable(
        bin_path / "uv",
        f'#!/bin/sh\nprintf "uv %s\\n" "$*" >> "$HOOK_LOG"\nexit {uv_exit}\n',
    )
    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{bin_path}:{environment['PATH']}",
            "HOOK_LOG": str(log),
            "FABRIC_ENABLED": "true",
            "FABRIC_ACCEPTANCE_ENABLED": "true",
            "FABRIC_ONTOLOGIES_JSON": '{"lamna-healthcare":{"workspaceId":"a","ontologyId":"b"}}',
            "FABRIC_ONTOLOGY_CATALOG_PATH": "catalog.json",
            "FABRIC_ONTOLOGY_INSPECTION_PATH": "inspection.json",
            "FABRIC_ONTOLOGY_ACCEPTANCE_REPORT": "report.json",
        }
    )
    return scripts / ROUTER.name, environment, log


def run(router: Path, environment: dict[str, str], phase: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 -- fixed copied repository hook router.
        [str(router), phase],
        cwd=router.parents[1],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )


def calls(log: Path) -> list[str]:
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def test_disabled_provider_hook_runs_no_provider_chain(tmp_path: Path) -> None:
    router, environment, log = harness(tmp_path)
    environment["FABRIC_ENABLED"] = "false"

    result = run(router, environment, "postdeploy")

    assert result.returncode == 0
    assert calls(log) == []


def test_postdeploy_without_acceptance_intent_leaves_fabric_configured(tmp_path: Path) -> None:
    router, environment, log = harness(tmp_path)
    environment["FABRIC_PROVIDER"] = "ontology"
    environment["FABRIC_ACCEPTANCE_ENABLED"] = "false"

    result = run(router, environment, "postdeploy")

    assert result.returncode == 0
    assert result.stdout.strip() == "SKIP: Fabric acceptance intentionally disabled"
    assert calls(log) == []


def test_same_tenant_smoke_skips_cross_tenant_postprovision_proof(tmp_path: Path) -> None:
    router, environment, log = harness(tmp_path)
    environment["FABRIC_PROVIDER"] = "ontology"
    environment["FABRIC_TOPOLOGY"] = "same_tenant_smoke"

    result = run(router, environment, "postprovision")

    assert result.returncode == 0
    assert result.stdout.strip() == "SKIP: production Fabric proof unavailable in same-tenant smoke topology"
    assert calls(log) == []


def test_semantic_mode_runs_only_semantic_chain(tmp_path: Path) -> None:
    router, environment, log = harness(tmp_path)
    environment["FABRIC_PROVIDER"] = "semantic_model"

    result = run(router, environment, "postdeploy")

    assert result.returncode == 0
    assert calls(log) == [
        "uv run python scripts/check-fabric-contract.py",
        "uv run python scripts/publish-fabric-contract.py",
        "run-fabric-acceptance.sh ",
    ]


def test_ontology_mode_runs_probe_contract_publication_and_acceptance_only(tmp_path: Path) -> None:
    router, environment, log = harness(tmp_path)
    environment["FABRIC_PROVIDER"] = "ontology"

    postprovision = run(router, environment, "postprovision")
    postdeploy = run(router, environment, "postdeploy")

    assert postprovision.returncode == 0
    assert postdeploy.returncode == 0
    observed = calls(log)
    assert observed[0] == "doctor-fabric-ontology.sh "
    assert observed[1] == "uv run python scripts/probe-fabric-ontology-auth.py"
    assert any("check-fabric-ontology-contract.py" in call for call in observed)
    assert any("publish-fabric-ontology-contract.py" in call for call in observed)
    assert observed[-1].startswith("run-fabric-ontology-acceptance.sh")
    assert all("check-fabric-contract.py" not in call for call in observed)


@pytest.mark.parametrize("provider", ["", "unknown"])
def test_enabled_unknown_provider_fails_before_any_chain(provider: str, tmp_path: Path) -> None:
    router, environment, log = harness(tmp_path)
    environment["FABRIC_PROVIDER"] = provider

    result = run(router, environment, "postdeploy")

    assert result.returncode != 0
    assert calls(log) == []


def test_selected_chain_failure_stops_publication_and_acceptance(tmp_path: Path) -> None:
    router, environment, log = harness(tmp_path, fail_name="uv")
    environment["FABRIC_PROVIDER"] = "semantic_model"

    result = run(router, environment, "postdeploy")

    assert result.returncode != 0
    assert calls(log) == ["uv run python scripts/check-fabric-contract.py"]
