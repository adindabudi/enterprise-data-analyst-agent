from pathlib import Path

import pytest
from eda_sandbox.app import create_app
from eda_sandbox.executor import ExecutionManager
from eda_sandbox.files import FileIndex
from fastapi.testclient import TestClient


def test_health_discloses_no_package_or_host_details(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "workspace"
    monkeypatch.setattr("eda_sandbox.files.IMPORTS", workspace / "inputs")
    monkeypatch.setattr("eda_sandbox.files.SOURCES", workspace / "sources")
    monkeypatch.setattr("eda_sandbox.files.OUTPUTS", workspace / "outputs")
    index = FileIndex()

    with TestClient(create_app(file_index=index, execution_manager=ExecutionManager(index.path_for))) as client:
        assert client.get("/health/live").json() == {"status": "alive"}
        assert client.get("/health/ready").json() == {"status": "ready"}
