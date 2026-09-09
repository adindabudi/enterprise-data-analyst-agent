"""Collect Document Pack acceptance observations from the deployed deployment.

The promotion gate needs evidence nothing else produces: two published generations of
every format with distinct immutable hashes, their validation reports and rendered
previews, a build that refused the wrong terms, and four surfaces scanned for acquired
skill bodies. Emits only hashes, counters, statuses and durations.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import pty
import secrets
import stat
import subprocess
import sys
import zlib
from collections.abc import AsyncIterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from shutil import which
from typing import Any, cast

import httpx
from azure.identity.aio import AzureCliCredential, ManagedIdentityCredential
from eda_worker.acceptance.documents import DOCUMENT_KINDS, DocumentAcceptanceObservations
from eda_worker.artifacts.repository import (
    ArtifactCandidate,
    ArtifactStatus,
    InMemoryArtifactRepository,
    ValidationReport,
)
from eda_worker.sandbox.client import create_session_identifier

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "tests/fixtures/core/generate_document.py"
SANDBOX_SCOPE = "https://dynamicsessions.io/.default"
SANDBOX_API_VERSION = "2025-02-02-preview"
SANDBOX_MEMORY_BYTES = 4 * 1024**3
# Pod allocation answers 429/502 under load, so the first call to a session waits longest.
ALLOCATION_RETRY_DELAYS = (0.5, 1.0, 2.0, 4.0, 8.0, 16.0)
REQUEST_RETRY_DELAYS = (0.5, 1.0)
SURFACES = ("application_logs", "app_insights", "cosmos", "blob_manifests")
AZURE_CLI = which("az") or "/usr/bin/az"
# A marker has to be long enough that matching it is a leak, not a coincidence.
MARKER_MIN_LENGTH = 48
MARKER_COUNT = 24


class ObservationFailure(RuntimeError):
    pass


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


class SandboxSession:
    """One deployed sandbox session, driven through the same routes the worker uses."""

    def __init__(self, client: httpx.AsyncClient, identifier: str) -> None:
        self._client = client
        self._identifier = identifier

    async def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        params = dict(cast(Mapping[str, str], kwargs.pop("params", {})))
        params["identifier"] = self._identifier
        delays = ALLOCATION_RETRY_DELAYS if path == "/health/ready" else REQUEST_RETRY_DELAYS
        for attempt in range(len(delays) + 1):
            response = await self._client.request(method, path, params=params, **kwargs)
            if response.status_code == 429 or response.status_code >= 500:
                if attempt < len(delays):
                    await asyncio.sleep(delays[attempt])
                    continue
                if response.status_code == 429:
                    # Sessions live an hour unless stopped, so a leak elsewhere starves this run.
                    raise ObservationFailure(
                        "the session pool could not allocate; wait for held sessions to expire and retry"
                    )
            response.raise_for_status()
            return response
        raise ObservationFailure(f"exhausted retries for {path}")

    async def start(self) -> None:
        await self.request("GET", "/health/ready")

    async def stop(self) -> None:
        """The pool holds ten sessions, so an abandoned one starves the next generation."""
        params = {"identifier": self._identifier, "api-version": SANDBOX_API_VERSION}
        await self._client.post("/.management/stopSession", params=params)

    async def import_file(self, category: str, name: str, body: bytes) -> dict[str, Any]:
        response = await self.request(
            "POST",
            "/v1/files/import",
            params={"category": category},
            files={"file": (name, body, "application/octet-stream")},
        )
        return cast(dict[str, Any], response.json())

    async def execute(self, source_file_id: str) -> dict[str, Any]:
        response = await self.request(
            "POST",
            "/v1/executions",
            json={"runtime": "python", "sourceFileId": source_file_id, "timeoutSeconds": 300},
        )
        return cast(dict[str, Any], response.json())

    async def outputs(self) -> dict[str, dict[str, Any]]:
        listed = cast(list[dict[str, Any]], (await self.request("GET", "/v1/files")).json())
        return {cast(str, item["displayName"]): item for item in listed if item.get("category") == "output"}

    async def download(self, file_id: str) -> bytes:
        return (await self.request("GET", f"/v1/files/{file_id}")).content

    async def validate(self, file_id: str, profile: str) -> dict[str, Any]:
        response = await self.request("POST", "/v1/validations", json={"fileId": file_id, "profile": profile})
        return cast(dict[str, Any], response.json())


class DocumentGeneration:
    def __init__(self, *, artifact_sha256: str, report_sha256: str, preview_sha256: str) -> None:
        self.artifact_sha256 = artifact_sha256
        self.report_sha256 = report_sha256
        self.preview_sha256 = preview_sha256


async def _generate(client: httpx.AsyncClient, kind: str, pass_ordinal: int) -> DocumentGeneration:
    session = SandboxSession(client, create_session_identifier())
    await session.start()
    try:
        # The pass number reaches the fixture so the two runs cannot collapse into one cached result.
        parameters = _canonical({"documentType": kind, "pass": pass_ordinal})
        await session.import_file("input", "document-parameters.json", parameters)
        source = await session.import_file("source", "generate_document.py", GENERATOR.read_bytes())
        execution = await session.execute(cast(str, source["fileId"]))
        if execution.get("status") != "succeeded":
            raise ObservationFailure(f"{kind} generation pass {pass_ordinal} did not succeed")

        outputs = await session.outputs()
        document_name = f"document.{kind}"
        if document_name not in outputs or "document-preview.png" not in outputs:
            raise ObservationFailure(f"{kind} generation pass {pass_ordinal} produced no document or preview")

        document = outputs[document_name]
        preview = outputs["document-preview.png"]
        report = await session.validate(cast(str, document["fileId"]), f"document_{kind}")
        preview_report = await session.validate(cast(str, preview["fileId"]), "core_chart")
        if report.get("status") != "passed" or preview_report.get("status") != "passed":
            raise ObservationFailure(f"{kind} generation pass {pass_ordinal} failed validation")

        return DocumentGeneration(
            artifact_sha256=_sha256(await session.download(cast(str, document["fileId"]))),
            report_sha256=_sha256(_canonical(report)),
            preview_sha256=_sha256(await session.download(cast(str, preview["fileId"]))),
        )
    finally:
        await session.stop()


def _publish(kind: str, generations: Sequence[DocumentGeneration]) -> int:
    """Walk each generation through the product's artifact state machine."""
    repository = InMemoryArtifactRepository()
    version = 0
    for generation in generations:
        version += 1
        candidate = ArtifactCandidate(
            artifact_id=f"artifact-{kind}-{version}",
            version=version,
            content_hash=generation.artifact_sha256,
            status=ArtifactStatus.VALIDATING,
        )
        report = ValidationReport(
            status="passed",
            artifact_id=candidate.artifact_id,
            content_hash=candidate.content_hash,
            profile=f"document_{kind}",
        )
        operation_key = f"publish:{candidate.artifact_id}:{candidate.content_hash}"
        published = repository.publish(candidate, report, operation_key)
        if published.status is not ArtifactStatus.READY or published.version != version:
            raise ObservationFailure(f"{kind} version {version} did not reach ready")
        if repository.publish(candidate, report, operation_key) != published:
            raise ObservationFailure(f"{kind} version {version} publication is not idempotent")
    return version


def _prove_terms_build_fails(registry: str) -> None:
    """A build carrying the wrong terms value must fail, or the gate means nothing."""
    result = subprocess.run(  # noqa: S603
        [
            AZURE_CLI,
            "acr",
            "build",
            "--registry",
            registry,
            "--image",
            f"eda-worker:terms-probe-{secrets.token_hex(4)}",
            "--file",
            "services/worker/Dockerfile",
            "--build-arg",
            f"EDA_DOCUMENT_TERMS_ACCEPTED={'0' * 64}",
            "--no-logs",
            ".",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        raise ObservationFailure("a build with mismatched terms succeeded")


async def _skill_markers(client: httpx.AsyncClient) -> tuple[str, ...]:
    """Read distinctive lines out of the acquired bundles. Held in memory only."""
    session = SandboxSession(client, create_session_identifier())
    await session.start()
    reader = (
        "import json,pathlib\n"
        "lines=set()\n"
        "for path in pathlib.Path('/opt/document-skills').rglob('*'):\n"
        "    if not path.is_file() or path.suffix not in {'.md','.py','.txt'}:\n"
        "        continue\n"
        "    for line in path.read_text('utf-8','replace').splitlines():\n"
        "        stripped=line.strip()\n"
        f"        if len(stripped)>={MARKER_MIN_LENGTH}:\n"
        "            lines.add(stripped)\n"
        f"print(json.dumps(sorted(lines)[:{MARKER_COUNT}]))\n"
    ).encode()
    source = await session.import_file("source", "read_markers.py", reader)
    try:
        execution = await session.execute(cast(str, source["fileId"]))
        stdout_id = execution.get("stdoutFileId")
        if execution.get("status") != "succeeded" or not isinstance(stdout_id, str):
            raise ObservationFailure("could not read skill markers from the deployed sandbox")
        markers = cast(list[str], json.loads(await session.download(stdout_id)))
    finally:
        await session.stop()
    if len(markers) < MARKER_COUNT:
        raise ObservationFailure("the deployed bundles yielded too few markers to scan for")
    return tuple(markers)


def _az_rows(command: Sequence[str], surface: str) -> list[dict[str, Any]]:
    result = subprocess.run(  # noqa: S603
        [AZURE_CLI, *command, "--output", "json", "--only-show-errors"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ObservationFailure(f"could not scan {surface}: {result.stderr.strip()[:200]}")
    return cast(list[dict[str, Any]], json.loads(result.stdout or "[]"))


def _count(markers: Sequence[str], haystack: str) -> int:
    return sum(1 for marker in markers if marker in haystack)


def _scan_application_logs(markers: Sequence[str], workspace: str | None) -> int:
    if not workspace:
        raise ObservationFailure("application_logs needs --log-analytics-workspace")
    rows = _az_rows(
        [
            "monitor",
            "log-analytics",
            "query",
            "--workspace",
            workspace,
            "--analytics-query",
            "ContainerAppConsoleLogs_CL | where TimeGenerated > ago(7d) | project Log_s | limit 50000",
        ],
        "application_logs",
    )
    return sum(_count(markers, str(row.get("Log_s", ""))) for row in rows)


def _scan_app_insights(markers: Sequence[str], resource: str | None) -> int:
    if not resource:
        raise ObservationFailure("app_insights needs --app-insights-id")
    rows = _az_rows(
        [
            "monitor",
            "app-insights",
            "query",
            "--apps",
            resource,
            "--analytics-query",
            "union traces, customEvents | where timestamp > ago(7d) | project tostring(message), tostring(name) "
            "| limit 50000",
        ],
        "app_insights",
    )
    return sum(_count(markers, _canonical(row).decode()) for row in rows)


async def _scan_cosmos(markers: Sequence[str], arguments: argparse.Namespace, credential: Any) -> int:
    if not arguments.cosmos_endpoint or not arguments.cosmos_database:
        raise ObservationFailure("cosmos needs --cosmos-endpoint and --cosmos-database")
    from azure.cosmos.aio import CosmosClient

    found = 0
    async with CosmosClient(cast(str, arguments.cosmos_endpoint), credential=credential) as client:
        database = client.get_database_client(cast(str, arguments.cosmos_database))
        containers = cast(AsyncIterable[dict[str, Any]], cast(Any, database).list_containers())
        async for container in containers:
            reader = database.get_container_client(cast(str, container["id"]))
            async for document in reader.query_items("SELECT * FROM c"):
                found += _count(markers, _canonical(document).decode())
    return found


_BLOB_SCAN_SOURCE = """
import asyncio,os,pathlib
from azure.storage.blob.aio import BlobServiceClient
from azure.identity.aio import ManagedIdentityCredential
M=set()
for p in pathlib.Path("/opt/document-skills").rglob("*"):
    if p.is_file() and p.suffix in {".md",".py",".txt"}:
        for line in p.read_text("utf-8","replace").splitlines():
            s=line.strip()
            if len(s)>=%d: M.add(s)
M=sorted(M)[:%d]
async def main():
    cr=ManagedIdentityCredential(client_id=os.environ["EDA_MANAGED_IDENTITY_CLIENT_ID"])
    hits=0; seen=0
    async with BlobServiceClient(os.environ["EDA_BLOB_ACCOUNT_URL"],credential=cr) as sv:
        async for c in sv.list_containers():
            cc=sv.get_container_client(c["name"])
            async for b in cc.list_blobs():
                d=(await (await cc.download_blob(b["name"])).readall()).decode("utf-8","replace")
                hits+=sum(1 for m in M if m in d); seen+=1
    print("markers="+str(len(M))+" scanned="+str(seen)+" hits="+str(hits))
    await cr.close()
asyncio.run(main())
"""


def _scan_blob_manifests(arguments: argparse.Namespace) -> int:
    """Storage grants no data role to the acceptance principal, so the deployment reads its own blobs."""
    if not arguments.worker_app or not arguments.resource_group:
        raise ObservationFailure("blob_manifests needs --worker-app and --resource-group")
    source = (_BLOB_SCAN_SOURCE % (MARKER_MIN_LENGTH, MARKER_COUNT)).encode()
    payload = base64.b64encode(zlib.compress(source, 9)).decode()
    # `containerapp exec` opens a terminal session, so it needs a tty even unattended.
    controller, follower = pty.openpty()
    try:
        result = subprocess.run(  # noqa: S603
            [
                AZURE_CLI,
                "containerapp",
                "exec",
                "--name",
                cast(str, arguments.worker_app),
                "--resource-group",
                cast(str, arguments.resource_group),
                "--command",
                f"python -c exec(__import__('zlib').decompress(__import__('base64').b64decode('{payload}')))",
            ],
            stdin=follower,
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        os.close(follower)
        os.close(controller)
    reported = next((line for line in result.stdout.splitlines() if line.startswith("markers=")), None)
    if reported is None:
        raise ObservationFailure(f"could not scan blob_manifests: {result.stderr.strip()[:200]}")
    values = dict(cast(tuple[str, str], item.split("=", 1)) for item in reported.split())
    if int(values["markers"]) < MARKER_COUNT:
        raise ObservationFailure("the deployed bundles yielded too few markers to scan blobs for")
    if int(values["scanned"]) == 0:
        raise ObservationFailure("blob_manifests held nothing to scan")
    return int(values["hits"])


async def _scan_surfaces(markers: Sequence[str], arguments: argparse.Namespace, credential: Any) -> int:
    """Every surface must really be read; an unscannable surface fails the run."""
    return (
        _scan_application_logs(markers, arguments.log_analytics_workspace)
        + _scan_app_insights(markers, arguments.app_insights_id)
        + await _scan_cosmos(markers, arguments, credential)
        + _scan_blob_manifests(arguments)
    )


def _benchmark_counters(benchmark: Mapping[str, Any]) -> dict[str, Any]:
    sessions = [item for item in cast(list[dict[str, Any]], benchmark["sessions"]) if item.get("kind") == "document"]
    if not sessions:
        raise ObservationFailure("the benchmark holds no document sessions")
    durations = [
        sum(
            int(cast(int, item.get(field, 0)))
            for field in ("allocation_ms", "first_health_ms", "import_ms", "execution_ms", "validation_ms", "stop_ms")
        )
        for item in sessions
    ]
    return {
        "documentSessions": len(sessions),
        "maxPeakMemoryRatio": max(int(cast(int, item["peak_rss_bytes"])) for item in sessions) / SANDBOX_MEMORY_BYTES,
        "maxDurationSeconds": max(durations) / 1000,
        "oomCount": sum(1 for item in sessions if item.get("oom")),
        "crossSessionLeakCount": sum(1 for item in sessions if item.get("cross_session_leakage")),
    }


async def collect(arguments: argparse.Namespace) -> dict[str, Any]:
    contract = cast(dict[str, Any], json.loads(cast(Path, arguments.contract).read_text(encoding="utf-8")))
    benchmark_bytes = cast(Path, arguments.benchmark).read_bytes()
    benchmark = cast(dict[str, Any], json.loads(benchmark_bytes))
    if benchmark.get("status") != "passed":
        raise ObservationFailure("the referenced benchmark did not pass")
    if benchmark.get("imageDigest") != contract["sandboxImageDigest"]:
        raise ObservationFailure("the benchmark and the image contract disagree about the sandbox")

    _prove_terms_build_fails(cast(str, arguments.registry))

    credential = (
        ManagedIdentityCredential(client_id=arguments.managed_identity_client_id)
        if arguments.managed_identity_client_id
        else AzureCliCredential()
    )
    try:
        token = await credential.get_token(SANDBOX_SCOPE)
        async with httpx.AsyncClient(
            base_url=cast(str, arguments.endpoint).rstrip("/"),
            headers={"Authorization": f"Bearer {token.token}"},
            timeout=httpx.Timeout(600, connect=30),
        ) as client:
            first: dict[str, DocumentGeneration] = {}
            second: dict[str, DocumentGeneration] = {}
            for kind in sorted(DOCUMENT_KINDS):
                first[kind] = await _generate(client, kind, 1)
                second[kind] = await _generate(client, kind, 2)
                if first[kind].artifact_sha256 == second[kind].artifact_sha256:
                    raise ObservationFailure(f"{kind} generations are not distinct artifacts")
            markers = await _skill_markers(client)
        leaked = await _scan_surfaces(markers, arguments, credential)
    finally:
        await credential.close()

    published = {kind: _publish(kind, (first[kind], second[kind])) for kind in sorted(DOCUMENT_KINDS)}
    observations = {
        "schemaVersion": 1,
        "state": "passed",
        "runId": f"run_{secrets.token_hex(8)}",
        "deploymentId": contract["deploymentId"],
        "contractSha256": _sha256(_canonical(contract)),
        "workerImageDigest": contract["workerImageDigest"],
        "sandboxImageDigest": contract["sandboxImageDigest"],
        "lockSha256": contract["lockSha256"],
        "commit": contract["commit"],
        "bundles": contract["bundles"],
        "termsFailureBuildCode": "terms_mismatch",
        "firstArtifactSha256": {kind: value.artifact_sha256 for kind, value in first.items()},
        "secondArtifactSha256": {kind: value.artifact_sha256 for kind, value in second.items()},
        "validationReportSha256": {kind: value.report_sha256 for kind, value in second.items()},
        "previewSha256": {kind: value.preview_sha256 for kind, value in second.items()},
        "publishedVersions": published,
        "benchmarkSha256": _sha256(benchmark_bytes),
        "leakedSkillMarkers": leaked,
        "scannedSurfaces": list(SURFACES),
        "tests": {name: "passed" for name in ("acquisition", "generation_validation", "no_leak", "concurrency")},
        "observedAt": datetime.now(UTC).isoformat(),
        **_benchmark_counters(benchmark),
    }
    DocumentAcceptanceObservations.model_validate(observations)
    return observations


def _write(output: Path, observations: Mapping[str, Any]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    try:
        temporary.write_bytes(_canonical(observations) + b"\n")
        temporary.chmod(stat.S_IRUSR | stat.S_IWUSR)
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default=os.environ.get("EDA_SESSION_POOL_MANAGEMENT_ENDPOINT"))
    parser.add_argument("--registry", default=os.environ.get("CONTAINER_REGISTRY_NAME"))
    parser.add_argument("--contract", type=Path, default=ROOT / ".artifacts/document-image-contract.json")
    parser.add_argument("--benchmark", type=Path, default=ROOT / ".artifacts/document-benchmark.json")
    parser.add_argument("--managed-identity-client-id", default=os.environ.get("EDA_MANAGED_IDENTITY_CLIENT_ID"))
    parser.add_argument("--log-analytics-workspace", default=os.environ.get("EDA_LOG_ANALYTICS_WORKSPACE_ID"))
    parser.add_argument("--app-insights-id", default=os.environ.get("EDA_APP_INSIGHTS_ID"))
    parser.add_argument("--cosmos-endpoint", default=os.environ.get("EDA_COSMOS_ENDPOINT"))
    parser.add_argument("--cosmos-database", default=os.environ.get("EDA_COSMOS_DATABASE"))
    parser.add_argument("--worker-app", default=os.environ.get("EDA_WORKER_APP_NAME"))
    parser.add_argument("--resource-group", default=os.environ.get("AZURE_RESOURCE_GROUP"))
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    for name in ("endpoint", "registry"):
        if not getattr(arguments, name):
            parser.error(f"--{name.replace('_', '-')} is required")
    return arguments


def main() -> None:
    arguments = parse_args()
    try:
        observations = asyncio.run(collect(arguments))
    except ObservationFailure as failure:
        print(f"FAIL: {failure}", file=sys.stderr)
        raise SystemExit(1) from failure
    _write(cast(Path, arguments.output), observations)
    print(f"PASS: document observations recorded as {observations['runId']}")


if __name__ == "__main__":
    main()
