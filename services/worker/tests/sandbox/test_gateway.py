from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from eda_contracts import ArtifactKind
from eda_contracts.tasks import TaskStatus
from eda_runtime_state.models import OperationStatus, TaskRecord
from eda_worker.sandbox.client import (
    ExecutionRecord,
    FileRecord,
    Runtime,
    ValidationProfile,
    ValidationResult,
    ValidationStatus,
)
from eda_worker.sandbox.gateway import DynamicSessionCapabilityGateway, InMemoryArtifactGatewayStore
from eda_worker.tools.contracts import (
    CapabilityStatus,
    ExecuteSandboxOperation,
    PublishArtifactOperation,
    SandboxRuntime,
    ValidateArtifactOperation,
)
from eda_worker.tools.contracts import (
    ValidationProfile as ToolValidationProfile,
)
from openpyxl import Workbook


@dataclass
class Operation:
    id: str
    status: OperationStatus
    immutable_result_ref: str | None = None


def _task(cancelled: bool = False) -> TaskRecord:
    now = datetime.now(UTC)
    return TaskRecord(
        id="task_12345678",
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
        session_id="ses_12345678",
        status=TaskStatus.ANALYZING,
        checkpoint_sequence=0,
        command_sequence=0,
        applied_command_sequence=0,
        cancellation_requested=cancelled,
        sourceMessageId="msg_source_12345678",
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(days=1),
    )


class FakeRuntimeRepository:
    def __init__(self, task: TaskRecord) -> None:
        self.task = task
        self.operations: dict[str, Operation] = {}

    async def resolve_task(self, task_id: str) -> TaskRecord | None:
        return self.task if task_id == self.task.id else None

    async def ensure_active_sandbox(self, task_id: str, proposed_identifier: str) -> str:
        if task_id != self.task.id:
            raise ValueError("task unavailable")
        if self.task.active_sandbox_id is not None:
            return self.task.active_sandbox_id
        self.task = self.task.model_copy(update={"active_sandbox_id": proposed_identifier})
        return proposed_identifier

    async def clear_active_sandbox(self, task_id: str, expected_identifier: str) -> TaskRecord | None:
        if task_id != self.task.id:
            return None
        if self.task.active_sandbox_id != expected_identifier:
            return self.task
        self.task = self.task.model_copy(update={"active_sandbox_id": None})
        return self.task

    async def begin_operation(self, task: TaskRecord, step_name: str, canonical_input: dict[str, Any]) -> Operation:
        digest = sha256(json.dumps(canonical_input, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        operation_id = f"op_{sha256(f'{task.id}:{step_name}:{digest}'.encode()).hexdigest()}"
        existing = self.operations.get(operation_id)
        if existing is not None:
            return existing
        operation = Operation(id=operation_id, status=OperationStatus.IN_PROGRESS)
        self.operations[operation_id] = operation
        return operation

    async def complete_operation(self, task: TaskRecord, operation_id: str, immutable_result_ref: str) -> Operation:
        del task
        operation = self.operations[operation_id]
        operation.status = OperationStatus.COMPLETED
        operation.immutable_result_ref = immutable_result_ref
        return operation


class FakeClient:
    def __init__(self) -> None:
        self.execution_calls = 0
        self.import_calls = 0
        self.imported: list[tuple[str, str, bytes]] = []
        self.stop_calls = 0
        self.validation_status = ValidationStatus.PASSED

    @staticmethod
    def create_identifier() -> str:
        return "ds_test_123456789"

    async def allocate(self, task_id: str, proposed_identifier: str) -> str:
        del task_id
        return proposed_identifier

    async def import_bytes(self, identifier: str, category: str, display_name: str, body: bytes) -> FileRecord:
        del identifier
        self.imported.append((category, display_name, body))
        self.import_calls += 1
        if category == "source":
            file_id = "file_source_12345678"
        elif display_name.endswith(".bin"):
            file_id = "file_input_12345678"
        else:
            file_id = "file_generic_12345678"
        return FileRecord(
            file_id=file_id,
            category=category,
            display_name=display_name,
            size_bytes=4,
            sha256="a" * 64,
        )

    async def execute(
        self,
        identifier: str,
        runtime: Runtime,
        source_file_id: str,
        timeout_seconds: int,
        parameters: dict[str, str | int | float | bool | None] | None = None,
    ) -> ExecutionRecord:
        del identifier, source_file_id, timeout_seconds, parameters
        self.execution_calls += 1
        return ExecutionRecord(
            execution_id="exec_12345678",
            status="succeeded",
            runtime=runtime,
            source_file_id="file_source_12345678",
            stdout_file_id="file_stdout_12345678",
            stderr_file_id=None,
            output_file_ids=("file_output_12345678",),
            return_code=0,
            duration_ms=5,
            peak_rss_bytes=10,
            cpu_time_ms=2,
        )

    async def download_file(self, identifier: str, file_id: str) -> bytes:
        del identifier
        if file_id == "file_output_12345678":
            return b"result-bytes"
        if file_id == "file_stdout_12345678":
            return b"stdout text"
        if file_id == "file_report_12345678":
            return b'{"status":"ok"}'
        return b""

    async def describe_file(self, identifier: str, file_id: str) -> FileRecord:
        del identifier
        display_name = "dashboard.html" if file_id == "file_output_12345678" else "output.txt"
        return FileRecord(
            file_id=file_id,
            category="output",
            display_name=display_name,
            size_bytes=12,
            sha256="c" * 64,
        )

    async def validate(self, identifier: str, file_id: str, profile: ValidationProfile) -> ValidationResult:
        del identifier, file_id, profile
        return ValidationResult(
            validation_id="validation_1234567890abcdef",
            file_id="file_input_12345678",
            profile=ValidationProfile.CORE_XLSX,
            status=self.validation_status,
            report=FileRecord(
                file_id="file_report_12345678",
                category="validation",
                display_name="report.json",
                size_bytes=16,
                sha256="b" * 64,
            ),
        )

    async def stop(self, identifier: str) -> None:
        del identifier
        self.stop_calls += 1


@pytest.mark.asyncio
async def test_completed_gateway_operation_reuses_artifacts() -> None:
    store = InMemoryArtifactGatewayStore()
    runtime = FakeRuntimeRepository(_task())
    client = FakeClient()
    gateway = DynamicSessionCapabilityGateway(client, runtime, store)
    input_ref = await store.persist_bytes("task_12345678", ArtifactKind.INPUT, "input.bin", b"input")
    runtime.task = runtime.task.model_copy(update={"input_artifacts": (input_ref,)})
    operation = ExecuteSandboxOperation(
        runtime=SandboxRuntime.PYTHON,
        source="print('hi')",
        input_artifacts=(input_ref,),
        timeout_seconds=30,
    )

    first = await gateway.execute("task_12345678", operation)
    second = await gateway.execute("task_12345678", operation)

    assert second == first
    assert client.execution_calls == 1


@pytest.mark.asyncio
async def test_execute_checks_cancellation_first() -> None:
    store = InMemoryArtifactGatewayStore()
    runtime = FakeRuntimeRepository(_task(cancelled=True))
    client = FakeClient()
    gateway = DynamicSessionCapabilityGateway(client, runtime, store)

    operation = ExecuteSandboxOperation(runtime=SandboxRuntime.PYTHON, source="print(1)")
    result = await gateway.execute("task_12345678", operation)

    assert result.status is CapabilityStatus.CANCELLED
    assert client.execution_calls == 0
    assert client.import_calls == 0


def test_optional_output_contract_preserves_legacy_operation_key() -> None:
    operation = ExecuteSandboxOperation(runtime=SandboxRuntime.PYTHON, source="print(1)")
    assert DynamicSessionCapabilityGateway._execute_canonical_input(operation) == {
        "runtime": "python", "source": "print(1)", "inputArtifacts": [],
        "parameters": {}, "timeoutSeconds": 120,
    }


@pytest.mark.asyncio
async def test_execute_imports_source_and_persists_output_and_stdout_refs() -> None:
    store = InMemoryArtifactGatewayStore()
    runtime = FakeRuntimeRepository(_task())
    client = FakeClient()
    gateway = DynamicSessionCapabilityGateway(client, runtime, store)
    input_ref = await store.persist_bytes("task_12345678", ArtifactKind.INPUT, "input.bin", b"input")
    runtime.task = runtime.task.model_copy(update={"input_artifacts": (input_ref,)})

    result = await gateway.execute(
        "task_12345678",
        ExecuteSandboxOperation(
            runtime=SandboxRuntime.PYTHON,
            source="print('result')",
            input_artifacts=(input_ref,),
            parameters={"threshold": 3},
            timeout_seconds=30,
        ),
    )

    assert result.status is CapabilityStatus.OK
    assert len(result.artifact_refs) == 1
    assert len(result.diagnostic_refs) == 1
    assert result.artifact_refs[0].kind is ArtifactKind.HTML
    payloads = [await store.read_bytes("task_12345678", ref) for ref in (*result.artifact_refs, *result.diagnostic_refs)]
    assert b"result-bytes" in payloads
    assert b"stdout text" in payloads


@pytest.mark.asyncio
async def test_validation_failure_returns_correctable_error_and_report_ref() -> None:
    store = InMemoryArtifactGatewayStore()
    runtime = FakeRuntimeRepository(_task())
    client = FakeClient()
    client.validation_status = ValidationStatus.FAILED
    gateway = DynamicSessionCapabilityGateway(client, runtime, store)
    candidate = await store.persist_bytes("task_12345678", ArtifactKind.XLSX, "output.xlsx", b"xlsx-data")

    result = await gateway.validate(
        "task_12345678",
        ValidateArtifactOperation(artifact=candidate, profile=ToolValidationProfile.CORE_XLSX),
    )

    assert result.status is CapabilityStatus.CORRECTABLE_ERROR
    assert len(result.artifact_refs) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(("kind", "profile"), [
    (ArtifactKind.HTML, ToolValidationProfile.WEB_ARTIFACT_HTML),
    (ArtifactKind.XLSX, ToolValidationProfile.CORE_XLSX),
    (ArtifactKind.MERMAID, ToolValidationProfile.CORE_MERMAID),
])
async def test_gateway_keeps_format_for_the_real_validator(
    tmp_path: Path, kind: ArtifactKind, profile: ToolValidationProfile,
) -> None:
    from eda_sandbox.contracts import ValidationProfile as LocalValidationProfile
    from eda_sandbox.validation import validate_path

    class ValidatingClient(FakeClient):
        async def validate(self, identifier: str, file_id: str, profile: ValidationProfile) -> ValidationResult:
            _, filename, content = self.imported[-1]
            candidate_path = tmp_path / filename
            candidate_path.write_bytes(content)
            try:
                validate_path(LocalValidationProfile(profile.value), candidate_path)
            except ValueError:
                self.validation_status = ValidationStatus.FAILED
            return await super().validate(identifier, file_id, profile)

    store = InMemoryArtifactGatewayStore()
    client = ValidatingClient()
    gateway = DynamicSessionCapabilityGateway(client, FakeRuntimeRepository(_task()), store)
    content = (
        b'<!doctype html><html><head><meta http-equiv="Content-Security-Policy" '
        b'content="default-src \'none\'; script-src \'unsafe-inline\'; style-src \'unsafe-inline\'; '
        b'img-src data: blob:; font-src data:; connect-src \'none\'; object-src \'none\'; '
        b'base-uri \'none\'; form-action \'none\'"><title>Revenue</title></head><body><h1>Revenue</h1>'
        b"<script>document.body.dataset.ready = 'true';</script></body></html>"
    )
    if kind is ArtifactKind.XLSX:
        buffer = BytesIO()
        Workbook().save(buffer)
        content = buffer.getvalue()
    elif kind is ArtifactKind.MERMAID:
        content = b"flowchart TD\n    Start --> End\n"
    candidate = await store.persist_bytes("task_12345678", kind, f"result.{kind.value}", content)

    result = await gateway.validate(
        "task_12345678",
        ValidateArtifactOperation(artifact=candidate, profile=profile),
    )

    assert result.status is CapabilityStatus.OK
    assert client.imported[-1][1].endswith(f".{kind.value}")


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", [ArtifactKind.HTML, ArtifactKind.XLSX, ArtifactKind.MERMAID])
async def test_gateway_preserves_generated_format_when_reimporting(kind: ArtifactKind) -> None:
    store = InMemoryArtifactGatewayStore()
    gateway = DynamicSessionCapabilityGateway(FakeClient(), FakeRuntimeRepository(_task()), store)
    candidate = await store.persist_bytes("task_12345678", kind, f"result.{kind.value}", b"candidate")

    filename = await gateway.input_filename("task_12345678", candidate)

    assert filename == f"{candidate.artifact_id}.{kind.value}"


@pytest.mark.asyncio
async def test_execution_requires_declared_outputs_and_separates_diagnostics() -> None:
    gateway = DynamicSessionCapabilityGateway(
        FakeClient(), FakeRuntimeRepository(_task()), InMemoryArtifactGatewayStore(),
    )

    result = await gateway.execute(
        "task_12345678",
        ExecuteSandboxOperation(
            runtime=SandboxRuntime.PYTHON, source="print('done')", expected_outputs=("analysis.xlsx",),
        ),
    )

    assert result.status is CapabilityStatus.CORRECTABLE_ERROR
    assert result.error_code == "missing_expected_outputs"
    assert "analysis.xlsx" in result.summary
    assert len(result.artifact_refs) == 1
    assert result.artifact_refs[0].kind is ArtifactKind.HTML
    assert len(result.diagnostic_refs) == 1
    assert result.diagnostic_refs[0].kind is ArtifactKind.DATA


@pytest.mark.asyncio
async def test_publish_requires_bound_passing_validation_report() -> None:
    store = InMemoryArtifactGatewayStore()
    runtime = FakeRuntimeRepository(_task())
    client = FakeClient()
    gateway = DynamicSessionCapabilityGateway(client, runtime, store)
    candidate = await store.persist_bytes("task_12345678", ArtifactKind.XLSX, "output.xlsx", b"xlsx-data")

    failed_report = await store.persist_validation(
        "task_12345678",
        candidate,
        profile=ToolValidationProfile.CORE_XLSX,
        report=b'{"status":"failed"}',
        status="failed",
    )
    blocked = await gateway.publish(
        "task_12345678",
        PublishArtifactOperation(artifact=candidate, validation_report=failed_report),
    )
    assert blocked.status is CapabilityStatus.BLOCKED

    passed_report = await store.persist_validation(
        "task_12345678",
        candidate,
        profile=ToolValidationProfile.CORE_XLSX,
        report=b'{"status":"passed"}',
        status="passed",
    )
    published = await gateway.publish(
        "task_12345678",
        PublishArtifactOperation(artifact=candidate, validation_report=passed_report),
    )
    assert published.status is CapabilityStatus.OK
    assert len(published.artifact_refs) == 1


@pytest.mark.asyncio
async def test_stop_task_stops_known_session() -> None:
    store = InMemoryArtifactGatewayStore()
    runtime = FakeRuntimeRepository(_task())
    client = FakeClient()
    gateway = DynamicSessionCapabilityGateway(client, runtime, store)
    input_ref = await store.persist_bytes("task_12345678", ArtifactKind.INPUT, "input.bin", b"input")
    runtime.task = runtime.task.model_copy(update={"input_artifacts": (input_ref,)})

    await gateway.execute(
        "task_12345678",
        ExecuteSandboxOperation(runtime=SandboxRuntime.PYTHON, source="print('hi')", input_artifacts=(input_ref,)),
    )
    await gateway.stop_task("task_12345678")

    assert client.stop_calls == 1


@pytest.mark.asyncio
async def test_stop_task_propagates_failure_and_preserves_sandbox_for_retry() -> None:
    class FailingStopClient(FakeClient):
        async def stop(self, identifier: str) -> None:
            raise RuntimeError("sandbox deletion unavailable")

    value = _task().model_copy(update={"active_sandbox_id": "sandbox-existing"})
    runtime = FakeRuntimeRepository(value)
    gateway = DynamicSessionCapabilityGateway(FailingStopClient(), runtime, InMemoryArtifactGatewayStore())
    with pytest.raises(RuntimeError, match="sandbox deletion unavailable"):
        await gateway.stop_task(value.id)
    assert (await runtime.resolve_task(value.id)).active_sandbox_id == "sandbox-existing"
