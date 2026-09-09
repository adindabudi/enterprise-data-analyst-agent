from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/run-sandbox-benchmark.py"


def load_module():
    specification = importlib.util.spec_from_file_location("run_sandbox_benchmark", SCRIPT)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def test_benchmark_uses_the_production_session_identifier_generator() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "create_session_identifier()" in source
    assert "secrets.token_urlsafe(32)" not in source


def test_custom_session_not_found_recognizes_live_azure_400_shape() -> None:
    module = load_module()
    request = httpx.Request("POST", "https://pool.example/.management/getSession")

    missing = httpx.Response(
        400,
        request=request,
        json={"error": {"code": "SessionWithIdentifierNotFound", "message": "bounded"}},
    )
    invalid = httpx.Response(
        400,
        request=request,
        json={"error": {"code": "SessionRequestValidationFailed", "message": "bounded"}},
    )

    assert module._session_is_missing(missing) is True
    assert module._session_is_missing(invalid) is False


@pytest.mark.parametrize("document_type", ["pptx", "docx", "pdf", "xlsx"])
def test_document_fixture_reads_opaque_indexed_parameter_filename(tmp_path: Path, document_type: str) -> None:
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    binary_directory = tmp_path / "bin"
    binary_directory.mkdir()
    libreoffice = binary_directory / "libreoffice"
    libreoffice.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    libreoffice.chmod(0o755)
    pdftoppm = binary_directory / "pdftoppm"
    pdftoppm.write_text(
        "#!/usr/bin/env python3\nfrom pathlib import Path\nimport sys\nPath(sys.argv[-1] + '.png').write_bytes(b'preview')\n",
        encoding="utf-8",
    )
    pdftoppm.chmod(0o755)
    (inputs / "file_opaque_12345678.json").write_text(json.dumps({"documentType": document_type}), encoding="utf-8")

    result = subprocess.run(  # noqa: S603 - runs the trusted benchmark script under test
        [sys.executable, str(ROOT / "tests/fixtures/core/generate_document.py")],
        cwd=tmp_path,
        env={
            **os.environ,
            "EDA_BENCHMARK_CHILD_SECONDS": "0",
            "NODE_PATH": str(ROOT / "services/sandbox/node_modules"),
            "PATH": f"{binary_directory}:{os.environ['PATH']}",
        },
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert (tmp_path / f"outputs/document.{document_type}").is_file()
    assert (tmp_path / "outputs/document-preview.png").is_file()


@pytest.mark.asyncio
async def test_health_allocation_retries_transient_capacity_backpressure(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_module()
    attempts = 0
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 7:
            return httpx.Response(429, request=request)
        return httpx.Response(200, request=request, json={"status": "ready"})

    async def record_delay(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr(module.asyncio, "sleep", record_delay)
    runner = module.BenchmarkRunner(
        endpoint="https://pool.example",
        token="not-a-real-token",  # noqa: S106 - synthetic test token
        image_digest="sha256:test",
    )
    await runner.client.aclose()
    runner.client = httpx.AsyncClient(
        base_url="https://pool.example",
        headers={"Authorization": "Bearer not-a-real-token"},
        transport=httpx.MockTransport(handler),
    )
    try:
        response = await runner._request("GET", "/health/ready", "ds-1234567890abcdef")
    finally:
        await runner.close()

    assert response.status_code == 200
    assert attempts == 7
    assert delays == [0.5, 1.0, 2.0, 4.0, 8.0, 16.0]
