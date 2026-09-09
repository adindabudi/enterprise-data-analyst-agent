from __future__ import annotations

from pathlib import Path

import pytest
from eda_sandbox.app import create_app
from eda_sandbox.contracts import ExecutionRecord, ExecutionRequest
from eda_sandbox.files import FileIndex
from fastapi.testclient import TestClient


class FakeExecutionManager:
    def __init__(self) -> None:
        self.requests: list[ExecutionRequest] = []

    def run(self, request: ExecutionRequest) -> ExecutionRecord:
        self.requests.append(request)
        return ExecutionRecord(
            execution_id="exec_12345678",
            status="succeeded",
            runtime=request.runtime,
            source_file_id=request.source_file_id,
            return_code=0,
            duration_ms=10,
            peak_rss_bytes=1,
            cpu_time_ms=1,
        )


@pytest.fixture
def file_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FileIndex:
    workspace = tmp_path / "workspace"
    monkeypatch.setattr("eda_sandbox.files.IMPORTS", workspace / "inputs")
    monkeypatch.setattr("eda_sandbox.files.SOURCES", workspace / "sources")
    monkeypatch.setattr("eda_sandbox.files.OUTPUTS", workspace / "outputs")
    return FileIndex()


def test_source_import_download_and_execution_are_opaque(file_index: FileIndex) -> None:
    execution_manager = FakeExecutionManager()
    app = create_app(file_index=file_index, execution_manager=execution_manager)

    with TestClient(app) as client:
        imported = client.post(
            "/v1/files/import",
            params={"category": "source"},
            files={"file": ("analysis.py", b"print('safe')\n", "text/x-python")},
        )
        assert imported.status_code == 200
        record = imported.json()
        assert record["fileId"].startswith("file_")
        assert "path" not in record
        assert client.get("/v1/files").json() == []

        downloaded = client.get(f"/v1/files/{record['fileId']}")
        assert downloaded.status_code == 200
        assert downloaded.content == b"print('safe')\n"

        executed = client.post(
            "/v1/executions",
            json={"runtime": "python", "sourceFileId": record["fileId"], "timeoutSeconds": 30},
        )
        assert executed.status_code == 200
        assert executed.json()["executionId"] == "exec_12345678"
        assert execution_manager.requests[0].source_file_id == record["fileId"]
        assert client.get("/v1/executions/exec_12345678").json()["status"] == "succeeded"


def test_validation_returns_indexed_content_free_report(file_index: FileIndex) -> None:
    safe = file_index.import_bytes("output", "report.html", b"<!doctype html><title>Safe</title>")
    unsafe = file_index.import_bytes("output", "unsafe.html", b'<img src="https://example.test/pixel.png">')
    app = create_app(file_index=file_index, execution_manager=FakeExecutionManager())

    with TestClient(app) as client:
        passed = client.post("/v1/validations", json={"fileId": safe.file_id, "profile": "core_html"})
        assert passed.status_code == 200
        assert passed.json()["status"] == "passed"
        assert passed.json()["report"]["category"] == "validation"

        failed = client.post("/v1/validations", json={"fileId": unsafe.file_id, "profile": "core_html"})
        assert failed.status_code == 200
        assert failed.json()["status"] == "failed"
        report = client.get(f"/v1/files/{failed.json()['report']['fileId']}").text
        assert "https://example.test" not in report
        assert str(file_index.path_for(unsafe.file_id)) not in report

        listed = client.get("/v1/files").json()
        assert {item["category"] for item in listed} == {"output", "validation"}

        missing = client.get("/v1/files/file_missing123")
        assert missing.status_code == 404
        assert set(missing.json()) == {"code", "message", "correlationId"}
