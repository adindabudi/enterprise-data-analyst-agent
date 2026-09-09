from __future__ import annotations

import argparse
import asyncio
import io
import json
import math
import os
import secrets
import subprocess
import time
import zipfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from shutil import which
from typing import Any, Literal, cast

import httpx
import openpyxl
from azure.identity.aio import AzureCliCredential, ManagedIdentityCredential
from eda_artifacts.archive import inspect_archive
from eda_worker.sandbox.benchmark import BenchmarkFailure, SessionMetric, evaluate_benchmark
from eda_worker.sandbox.client import create_session_identifier

SCOPE = "https://dynamicsessions.io/.default"
API_VERSION = "2025-02-02-preview"
ALLOCATION_RETRY_DELAYS = (0.5, 1.0, 2.0, 4.0, 8.0, 16.0)
REQUEST_RETRY_DELAYS = (0.5, 1.0)
ROOT = Path(__file__).resolve().parents[1]
CORE_GENERATOR = ROOT / "tests/fixtures/core/generate_outputs.py"
ISOLATION_TRAILER = ROOT / "tests/fixtures/core/benchmark_isolation_trailer.py"
DOCUMENT_GENERATOR = ROOT / "tests/fixtures/core/generate_document.py"
DOCUMENT_TYPES = ("pptx", "docx", "pdf", "xlsx")
CONTROL_FIXTURE = ROOT / "tests/fixtures/core/fy2026-control.json"
GIT = which("git") or "/usr/bin/git"


class BenchmarkRunner:
    def __init__(self, *, endpoint: str, token: str, image_digest: str) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.image_digest = image_digest
        self.client = httpx.AsyncClient(
            base_url=self.endpoint,
            headers={"Authorization": f"Bearer {token}"},
            timeout=httpx.Timeout(360, connect=30),
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def run_wave(self, kind: Literal["core", "document"], count: int) -> list[SessionMetric]:
        return list(await asyncio.gather(*(self.run_session(kind, ordinal) for ordinal in range(count))))

    async def run_session(self, kind: Literal["core", "document"], ordinal: int) -> SessionMetric:
        identifier = create_session_identifier()
        allocation_ms = 0
        first_health_ms = 0
        import_ms = 0
        execution_ms = 0
        render_ms = 0
        validation_ms = 0
        peak_rss_bytes = 0
        cpu_time_ms = 0
        output_bytes = 0
        output_files = 0
        completed = False
        oom = False
        process_escape = False
        file_escape = False
        network_success = False
        cross_session_leakage = False
        stop_ms = 0
        lingering_after_stop = False
        failure_stage: str | None = None
        failure_code: str | None = None
        stage = "health"
        try:
            health_started = time.monotonic()
            health = await self._request("GET", "/health/ready", identifier)
            health.raise_for_status()
            allocation_ms = first_health_ms = _elapsed_ms(health_started)

            stage = "import"
            sentinel = secrets.token_bytes(32)
            expected_hashes: set[str] = set()
            import_started = time.monotonic()
            sentinel_record = await self._import(identifier, "input", "../../sentinel.bin", sentinel)
            expected_hashes.add(cast(str, sentinel_record["sha256"]))
            file_escape = sentinel_record.get("displayName") != "sentinel.bin"

            if kind == "core":
                input_workbook = _build_input_workbook()
                expected_hashes.add(
                    cast(str, (await self._import(identifier, "input", "fy2026.xlsx", input_workbook))["sha256"])
                )
                parameters = json.dumps(
                    {"randomSeed": ordinal + 1, "imageDigest": self.image_digest},
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode()
                expected_hashes.add(
                    cast(str, (await self._import(identifier, "input", "parameters.json", parameters))["sha256"])
                )
                if ordinal == 0:
                    large_input = b"\x00" * (50 * 1024 * 1024)
                    expected_hashes.add(
                        cast(str, (await self._import(identifier, "input", "valid-50mib.bin", large_input))["sha256"])
                    )
                    expansion_fixture = _safe_expansion_fixture()
                    expected_hashes.add(
                        cast(
                            str,
                            (await self._import(identifier, "input", "safe-expansion.zip", expansion_fixture))[
                                "sha256"
                            ],
                        )
                    )
                source = CORE_GENERATOR.read_bytes() + b"\n" + ISOLATION_TRAILER.read_bytes()
                source_name = "core-benchmark.py"
            else:
                document_parameters = json.dumps(
                    {"documentType": DOCUMENT_TYPES[ordinal % len(DOCUMENT_TYPES)]},
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode()
                expected_hashes.add(
                    cast(
                        str,
                        (
                            await self._import(
                                identifier,
                                "input",
                                "document-parameters.json",
                                document_parameters,
                            )
                        )["sha256"],
                    )
                )
                source = DOCUMENT_GENERATOR.read_bytes()
                source_name = "document-benchmark.py"
            source_record = await self._import(identifier, "source", source_name, source)
            import_ms = _elapsed_ms(import_started)

            stage = "execution"
            execution_started = time.monotonic()
            execution_response = await self._request(
                "POST",
                "/v1/executions",
                identifier,
                json={"runtime": "python", "sourceFileId": source_record["fileId"], "timeoutSeconds": 300},
            )
            execution_response.raise_for_status()
            execution = cast(dict[str, Any], execution_response.json())
            execution_ms = _elapsed_ms(execution_started)
            peak_rss_bytes = _integer(execution.get("peakRssBytes"))
            cpu_time_ms = _integer(execution.get("cpuTimeMs"))
            return_code = execution.get("returnCode")
            oom = return_code in {-9, 137}
            if execution.get("status") != "succeeded":
                raise RuntimeError("benchmark execution failed")

            stage = "execution_evidence"
            stdout_id = execution.get("stdoutFileId")
            if not isinstance(stdout_id, str):
                raise RuntimeError("benchmark execution returned no stdout evidence")
            stdout_response = await self._request("GET", f"/v1/files/{stdout_id}", identifier)
            stdout_response.raise_for_status()
            evidence = _last_json_line(stdout_response.text)
            observed_hashes = set(cast(list[str], evidence.get("inputHashes", [])))
            cross_session_leakage = observed_hashes != expected_hashes
            network_success = evidence.get("networkSuccess") is True
            render_ms = _integer(evidence.get("renderMs"))
            child_pid = evidence.get("childPid")
            if isinstance(child_pid, int):
                stage = "process_probe"
                process_escape = await self._probe_process(identifier, child_pid)

            stage = "file_listing"
            list_response = await self._request("GET", "/v1/files", identifier)
            list_response.raise_for_status()
            records = cast(list[dict[str, Any]], list_response.json())
            output_records = [record for record in records if record.get("category") == "output"]
            output_bytes = sum(_integer(record.get("sizeBytes")) for record in output_records)
            output_files = len(output_records)
            stage = "validation"
            validation_started = time.monotonic()
            if kind == "core":
                await self._validate_core(identifier, output_records)
            else:
                await self._validate_document(identifier, output_records, ordinal)
            validation_ms = _elapsed_ms(validation_started)
            completed = True
        except (httpx.HTTPError, OSError, RuntimeError, ValueError, KeyError, json.JSONDecodeError) as error:
            completed = False
            failure_stage = stage
            failure_code = _failure_code(error)
        finally:
            stop_ms, lingering_after_stop = await self._stop(identifier)

        return SessionMetric(
            kind=kind,
            ordinal=ordinal,
            completed=completed,
            allocation_ms=allocation_ms,
            first_health_ms=first_health_ms,
            import_ms=import_ms,
            execution_ms=execution_ms,
            render_ms=render_ms,
            validation_ms=validation_ms,
            stop_ms=stop_ms,
            peak_rss_bytes=peak_rss_bytes,
            cpu_time_ms=cpu_time_ms,
            output_bytes=output_bytes,
            output_files=output_files,
            oom=oom,
            process_escape=process_escape,
            file_escape=file_escape,
            network_success=network_success,
            cross_session_leakage=cross_session_leakage,
            lingering_after_stop=lingering_after_stop,
            failure_stage=failure_stage,
            failure_code=failure_code,
        )

    async def _import(self, identifier: str, category: str, name: str, body: bytes) -> dict[str, Any]:
        response = await self._request(
            "POST",
            "/v1/files/import",
            identifier,
            params={"category": category},
            files={"file": (name, body, "application/octet-stream")},
        )
        response.raise_for_status()
        return cast(dict[str, Any], response.json())

    async def _validate_core(self, identifier: str, records: Sequence[Mapping[str, Any]]) -> None:
        profiles = {
            "analysis.xlsx": "core_xlsx",
            "report.html": "core_html",
            "report-desktop.png": "core_chart",
            "report-mobile.png": "core_chart",
            "revenue-chart.svg": "core_chart",
            "revenue-chart.png": "core_chart",
            "revenue-flow.mmd": "core_mermaid",
            "revenue-flow.svg": "core_mermaid",
            "revenue-flow.png": "core_mermaid",
            "task-manifest.json": "provenance",
        }
        by_name = {record.get("displayName"): record for record in records}
        for name, profile in profiles.items():
            record = by_name.get(name)
            if not isinstance(record, Mapping) or not isinstance(record.get("fileId"), str):
                raise RuntimeError("Core benchmark output is missing")
            response = await self._request(
                "POST",
                "/v1/validations",
                identifier,
                json={"fileId": record["fileId"], "profile": profile},
            )
            response.raise_for_status()
            if response.json().get("status") != "passed":
                raise RuntimeError("Core benchmark validation failed")

    async def _validate_document(
        self,
        identifier: str,
        records: Sequence[Mapping[str, Any]],
        ordinal: int,
    ) -> None:
        document_type = DOCUMENT_TYPES[ordinal % len(DOCUMENT_TYPES)]
        profiles = {
            f"document.{document_type}": f"document_{document_type}",
            "document-preview.png": "core_chart",
        }
        by_name = {record.get("displayName"): record for record in records}
        for name, profile in profiles.items():
            record = by_name.get(name)
            if not isinstance(record, Mapping) or not isinstance(record.get("fileId"), str):
                raise RuntimeError("document benchmark output is missing")
            response = await self._request(
                "POST",
                "/v1/validations",
                identifier,
                json={"fileId": record["fileId"], "profile": profile},
            )
            response.raise_for_status()
            if response.json().get("status") != "passed":
                raise RuntimeError("document benchmark validation failed")

    async def _probe_process(self, identifier: str, child_pid: int) -> bool:
        probe = (
            "from pathlib import Path\nimport json\n"
            f"print(json.dumps({{'processEscape': Path('/proc/{child_pid}').exists()}}, separators=(',', ':')))\n"
        ).encode()
        source = await self._import(identifier, "source", "process-probe.py", probe)
        response = await self._request(
            "POST",
            "/v1/executions",
            identifier,
            json={"runtime": "python", "sourceFileId": source["fileId"], "timeoutSeconds": 30},
        )
        response.raise_for_status()
        execution = cast(dict[str, Any], response.json())
        stdout_id = execution.get("stdoutFileId")
        if not isinstance(stdout_id, str):
            return True
        stdout = await self._request("GET", f"/v1/files/{stdout_id}", identifier)
        stdout.raise_for_status()
        return _last_json_line(stdout.text).get("processEscape") is True

    async def _stop(self, identifier: str) -> tuple[int, bool]:
        started = time.monotonic()
        lingering = True
        try:
            response = await self._request(
                "POST",
                "/.management/stopSession",
                identifier,
                params={"api-version": API_VERSION},
            )
            if response.status_code not in {200, 202, 204, 404}:
                response.raise_for_status()
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                status = await self._request(
                    "POST",
                    "/.management/getSession",
                    identifier,
                    params={"api-version": API_VERSION},
                )
                if _session_is_missing(status):
                    lingering = False
                    break
                status.raise_for_status()
                await asyncio.sleep(0.25)
        except httpx.HTTPError:
            lingering = True
        return _elapsed_ms(started), lingering

    async def _request(
        self,
        method: str,
        path: str,
        identifier: str,
        **kwargs: Any,
    ) -> httpx.Response:
        params = dict(cast(Mapping[str, Any], kwargs.pop("params", {})))
        params["identifier"] = identifier
        delays = ALLOCATION_RETRY_DELAYS if path == "/health/ready" else REQUEST_RETRY_DELAYS
        for attempt in range(len(delays) + 1):
            response = await self.client.request(method, path, params=params, **kwargs)
            if response.status_code != 429 and response.status_code < 500:
                return response
            if attempt < len(delays):
                await asyncio.sleep(delays[attempt])
            else:
                return response
        raise RuntimeError("unreachable benchmark retry state")


def _build_input_workbook() -> bytes:
    control = cast(dict[str, Any], json.loads(CONTROL_FIXTURE.read_text(encoding="utf-8")))
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    if sheet is None:
        raise RuntimeError("benchmark workbook has no active sheet")
    sheet.title = "Revenue"
    sheet.append(["Region", "Revenue"])
    for item in cast(list[dict[str, str]], control["rows"]):
        sheet.append([item["region"], float(item["revenue"])])
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _safe_expansion_fixture() -> bytes:
    block = secrets.token_bytes(64 * 1024)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        archive.writestr("expanded.bin", block * 50)
    payload = buffer.getvalue()
    inspect_archive(payload)
    return payload


def _last_json_line(text: str) -> dict[str, Any]:
    for line in reversed(text.splitlines()):
        if line.startswith("{"):
            value: object = json.loads(line)
            if isinstance(value, dict):
                return cast(dict[str, Any], value)
    raise ValueError("benchmark stdout contains no JSON evidence")


def _integer(value: object) -> int:
    return value if isinstance(value, int) and value >= 0 else 0


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


def _failure_code(error: Exception) -> str:
    if isinstance(error, httpx.HTTPStatusError):
        return f"http_{error.response.status_code}"
    if isinstance(error, httpx.TransportError):
        return "transport_error"
    if isinstance(error, json.JSONDecodeError):
        return "invalid_json"
    if isinstance(error, KeyError):
        return "missing_field"
    if isinstance(error, OSError):
        return "local_io_error"
    if isinstance(error, ValueError):
        return "invalid_value"
    return "runtime_error"


def _session_is_missing(response: httpx.Response) -> bool:
    if response.status_code == 404:
        return True
    if response.status_code != 400:
        return False
    try:
        body: object = response.json()
    except json.JSONDecodeError:
        return False
    if not isinstance(body, dict):
        return False
    raw_error = cast(dict[str, object], body).get("error")
    if not isinstance(raw_error, dict):
        return False
    error = cast(dict[str, object], raw_error)
    return error.get("code") == "SessionWithIdentifierNotFound"


def _percentiles(metrics: Sequence[SessionMetric], field: str) -> dict[str, int]:
    values = sorted(cast(int, getattr(metric, field)) for metric in metrics)
    return {
        "p50": values[max(0, math.ceil(0.50 * len(values)) - 1)],
        "p95": values[max(0, math.ceil(0.95 * len(values)) - 1)],
    }


def _git_commit() -> str:
    result = subprocess.run(  # noqa: S603 -- fixed Git command against the current repository.
        [GIT, "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    return result.stdout.strip()


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


async def run_benchmark(arguments: argparse.Namespace) -> dict[str, Any]:
    credential = (
        ManagedIdentityCredential(client_id=arguments.managed_identity_client_id)
        if arguments.managed_identity_client_id
        else AzureCliCredential()
    )
    try:
        token = await credential.get_token(SCOPE)
        runner = BenchmarkRunner(endpoint=arguments.endpoint, token=token.token, image_digest=arguments.image_digest)
        try:
            core_metrics = await runner.run_wave("core", 10)
            document_metrics = (
                await runner.run_wave("document", len(DOCUMENT_TYPES)) if arguments.include_documents else []
            )
        finally:
            await runner.close()
    finally:
        await credential.close()
    metrics = [*core_metrics, *document_metrics]
    try:
        summary = evaluate_benchmark(metrics, require_documents=arguments.include_documents)
        status = "passed"
        failure_code = None
    except BenchmarkFailure:
        summary = None
        status = "failed"
        failure_code = "benchmark_assertion_failed"
    report: dict[str, Any] = {
        "schemaVersion": "1.0",
        "status": status,
        "region": arguments.region,
        "poolApiVersion": API_VERSION,
        "profile": arguments.profile,
        "imageDigest": arguments.image_digest,
        "fixtureCommit": _git_commit(),
        "concurrency": {"core": 10, "document": len(DOCUMENT_TYPES) if arguments.include_documents else 0},
        "percentilesMs": {
            field: _percentiles(metrics, field)
            for field in (
                "allocation_ms",
                "first_health_ms",
                "import_ms",
                "execution_ms",
                "render_ms",
                "validation_ms",
                "stop_ms",
            )
        },
        "sessions": [metric.model_dump(mode="json", by_alias=True) for metric in metrics],
        "summary": summary.model_dump(mode="json", by_alias=True) if summary is not None else None,
        "failureCode": failure_code,
    }
    _atomic_json(arguments.output, report)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--endpoint",
        default=os.environ.get("EDA_SESSION_POOL_MANAGEMENT_ENDPOINT") or os.environ.get("EDA_SESSION_POOL_ENDPOINT"),
    )
    parser.add_argument("--image-digest", default=os.environ.get("EDA_SANDBOX_IMAGE_DIGEST"))
    parser.add_argument("--region", default=os.environ.get("AZURE_LOCATION"))
    parser.add_argument("--profile", default=os.environ.get("EDA_DEPLOYMENT_PROFILE", "demo"))
    parser.add_argument("--managed-identity-client-id", default=os.environ.get("EDA_MANAGED_IDENTITY_CLIENT_ID"))
    parser.add_argument("--include-documents", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    if not arguments.endpoint:
        parser.error("--endpoint or EDA_SESSION_POOL_MANAGEMENT_ENDPOINT is required")
    if not arguments.image_digest or not str(arguments.image_digest).startswith("sha256:"):
        parser.error("--image-digest or EDA_SANDBOX_IMAGE_DIGEST must be an immutable sha256 digest")
    if not arguments.region:
        parser.error("--region or AZURE_LOCATION is required")
    return arguments


def main() -> None:
    report = asyncio.run(run_benchmark(parse_args()))
    if report["status"] != "passed":
        print("FAIL: sandbox isolation benchmark failed")
        raise SystemExit(1)
    print("PASS: sandbox isolation benchmark completed")


if __name__ == "__main__":
    main()
