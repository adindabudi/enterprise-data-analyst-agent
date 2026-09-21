from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def synchronizer() -> Any:
    spec = importlib.util.spec_from_file_location("sync_bicep_outputs", ROOT / "scripts/sync-bicep-outputs.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sync_publishes_aliases_from_structured_azd_output(synchronizer: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    values = {name: f"output-{name}" for name in synchronizer.OUTPUT_ALIASES.values()}
    values["redisPort"] = 6380
    values["fabricAcceptanceJobId"] = ""
    writes: list[list[str]] = []
    monkeypatch.setattr(synchronizer.shutil, "which", lambda _name: "/tools/azd")

    def run(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        if command == ["/tools/azd", "env", "get-values", "--output", "json"]:
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(values))
        assert command[:3] == ["/tools/azd", "env", "set"]
        writes.append(command[3:])
        return subprocess.CompletedProcess(command, 0, stdout="")

    monkeypatch.setattr(synchronizer.subprocess, "run", run)

    assert synchronizer.main() == 0
    assert dict(writes) == synchronizer.environment_updates(values)
    assert dict(writes)["REDIS_PORT"] == "6380"
    assert dict(writes)["FABRIC_ACCEPTANCE_JOB_ID"] == ""


def test_sync_rejects_missing_outputs_before_writing(synchronizer: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr(synchronizer.shutil, "which", lambda _name: "/tools/azd")

    def run(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="{}")

    monkeypatch.setattr(synchronizer.subprocess, "run", run)

    assert synchronizer.main() == 1
    assert commands == [["/tools/azd", "env", "get-values", "--output", "json"]]


def test_sync_precedes_every_postprovision_consumer() -> None:
    configuration = yaml.safe_load((ROOT / "azure.yaml").read_text(encoding="utf-8"))
    steps = configuration["hooks"]["postprovision"]["run"].split(" && ")

    assert steps[0] == "uv run python scripts/sync-bicep-outputs.py"
    assert "./scripts/build-worker-image.sh" in steps[1:]
