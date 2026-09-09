from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest
from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.cosmos.exceptions import CosmosHttpResponseError, CosmosResourceNotFoundError
from eda_contracts import ArtifactKind, ArtifactRef
from eda_contracts.tasks import TaskStatus
from eda_runtime_state.models import TaskRecord
from eda_worker.sandbox.gateway import CosmosBlobArtifactGatewayStore
from eda_worker.tools.contracts import ValidationProfile


class FakeRuntime:
    def __init__(self, task: TaskRecord) -> None:
        self._task = task

    async def resolve_task(self, task_id: str) -> TaskRecord | None:
        return self._task if task_id == self._task.id else None

    async def ensure_active_sandbox(self, task_id: str, proposed_identifier: str) -> str:
        del task_id
        return proposed_identifier

    async def clear_active_sandbox(self, task_id: str, expected_identifier: str) -> TaskRecord | None:
        del task_id, expected_identifier
        return self._task


class FakeWorkspaceContainer:
    def __init__(self) -> None:
        self._items: dict[tuple[tuple[str, ...], str], dict[str, Any]] = {}
        self.read_calls: list[tuple[str, tuple[str, ...]]] = []

    @staticmethod
    def _partition_key(body: dict[str, Any]) -> tuple[str, ...]:
        return (
            str(body["tenantId"]),
            str(body["ownerObjectId"]),
            str(body["sessionId"]),
        )

    async def create_item(self, body: dict[str, Any]) -> dict[str, Any]:
        key = (self._partition_key(body), str(body["id"]))
        if key in self._items:
            raise CosmosHttpResponseError(status_code=409, message="conflict")
        self._items[key] = dict(body)
        return dict(body)

    async def read_item(self, item: str, partition_key: list[str]) -> dict[str, Any]:
        key = (tuple(str(part) for part in partition_key), item)
        self.read_calls.append((item, key[0]))
        value = self._items.get(key)
        if value is None:
            raise CosmosResourceNotFoundError(message="not found")
        return dict(value)


class FakeBlobDownloader:
    def __init__(self, content: bytes) -> None:
        self._content = content

    async def readall(self) -> bytes:
        return self._content


@dataclass
class _BlobEntry:
    content: bytes
    metadata: dict[str, str]


class FakeBlobClient:
    def __init__(self, container: FakeBlobContainer, name: str) -> None:
        self._container = container
        self._name = name

    async def upload_blob(self, data: bytes, overwrite: bool, metadata: dict[str, str]) -> None:
        if not overwrite and self._name in self._container._blobs:
            raise ResourceExistsError(message="exists")
        self._container._blobs[self._name] = _BlobEntry(content=bytes(data), metadata=dict(metadata))

    async def get_blob_properties(self) -> Any:
        entry = self._container._blobs.get(self._name)
        if entry is None:
            raise ResourceNotFoundError(message="not found")
        return SimpleNamespace(size=len(entry.content), metadata=dict(entry.metadata))

    async def download_blob(self) -> FakeBlobDownloader:
        entry = self._container._blobs.get(self._name)
        if entry is None:
            raise ResourceNotFoundError(message="not found")
        return FakeBlobDownloader(entry.content)


class FakeBlobContainer:
    def __init__(self) -> None:
        self._blobs: dict[str, _BlobEntry] = {}

    def get_blob_client(self, blob: str) -> FakeBlobClient:
        return FakeBlobClient(self, blob)

    def tamper_blob(self, blob_name: str, *, content: bytes, metadata: dict[str, str]) -> None:
        self._blobs[blob_name] = _BlobEntry(content=content, metadata=metadata)


def _task(task_id: str = "task_12345678") -> TaskRecord:
    now = datetime.now(UTC)
    return TaskRecord(
        id=task_id,
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
        session_id="ses_12345678",
        status=TaskStatus.ANALYZING,
        checkpoint_sequence=0,
        command_sequence=0,
        applied_command_sequence=0,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(days=1),
    )


@pytest.mark.asyncio
async def test_persist_bytes_conflict_retry_returns_same_ref_and_hpk_reads() -> None:
    task = _task()
    runtime = FakeRuntime(task)
    workspace = FakeWorkspaceContainer()
    blobs = FakeBlobContainer()
    store = CosmosBlobArtifactGatewayStore(runtime, cast(Any, workspace), cast(Any, blobs))

    first = await store.persist_bytes(task.id, ArtifactKind.DATA, "out.bin", b"same-bytes")
    second = await store.persist_bytes(task.id, ArtifactKind.DATA, "out.bin", b"same-bytes")

    assert first == second
    _ = await store.read_bytes(task.id, first)
    expected_hpk = (str(task.tenant_id), str(task.owner_object_id), task.session_id)
    assert any(partition == expected_hpk for _, partition in workspace.read_calls)


@pytest.mark.asyncio
async def test_read_rejects_digest_mismatch() -> None:
    task = _task()
    runtime = FakeRuntime(task)
    workspace = FakeWorkspaceContainer()
    blobs = FakeBlobContainer()
    store = CosmosBlobArtifactGatewayStore(runtime, cast(Any, workspace), cast(Any, blobs))

    ref = await store.persist_bytes(task.id, ArtifactKind.DATA, "out.bin", b"payload")
    bad = ArtifactRef(
        artifact_id=ref.artifact_id,
        version=ref.version,
        kind=ref.kind,
        sha256="0" * 64,
    )

    with pytest.raises(ValueError, match="digest"):
        await store.read_bytes(task.id, bad)


@pytest.mark.asyncio
async def test_task_isolation_blocks_other_task_reads() -> None:
    first_task = _task("task_12345678")
    second_task = _task("task_87654321")
    runtime = FakeRuntime(first_task)
    workspace = FakeWorkspaceContainer()
    blobs = FakeBlobContainer()
    store = CosmosBlobArtifactGatewayStore(runtime, cast(Any, workspace), cast(Any, blobs))

    ref = await store.persist_bytes(first_task.id, ArtifactKind.DATA, "out.bin", b"payload")
    store_other = CosmosBlobArtifactGatewayStore(
        FakeRuntime(second_task),
        cast(Any, workspace),
        cast(Any, blobs),
    )

    with pytest.raises(ValueError, match="unavailable"):
        await store_other.read_bytes(second_task.id, ref)


@pytest.mark.asyncio
async def test_publish_accepts_passing_validation_and_is_idempotent() -> None:
    task = _task()
    runtime = FakeRuntime(task)
    workspace = FakeWorkspaceContainer()
    blobs = FakeBlobContainer()
    store = CosmosBlobArtifactGatewayStore(runtime, cast(Any, workspace), cast(Any, blobs))

    candidate = await store.persist_bytes(task.id, ArtifactKind.XLSX, "report.xlsx", b"xlsx-content")
    report = await store.persist_validation(
        task.id,
        candidate,
        ValidationProfile.CORE_XLSX,
        b'{"status":"passed"}',
        "passed",
    )

    first = await store.publish(task.id, candidate, report)
    second = await store.publish(task.id, candidate, report)

    assert first == second
    assert first.version == candidate.version + 1
    assert first.artifact_id == candidate.artifact_id
    assert first.sha256 == candidate.sha256


@pytest.mark.asyncio
async def test_publish_rejects_failed_validation() -> None:
    task = _task()
    runtime = FakeRuntime(task)
    workspace = FakeWorkspaceContainer()
    blobs = FakeBlobContainer()
    store = CosmosBlobArtifactGatewayStore(runtime, cast(Any, workspace), cast(Any, blobs))

    candidate = await store.persist_bytes(task.id, ArtifactKind.XLSX, "report.xlsx", b"xlsx-content")
    failed = await store.persist_validation(
        task.id,
        candidate,
        ValidationProfile.CORE_XLSX,
        b'{"status":"failed"}',
        "failed",
    )

    with pytest.raises(ValueError, match="passing"):
        await store.publish(task.id, candidate, failed)


@pytest.mark.asyncio
async def test_blob_conflict_with_different_content_is_rejected() -> None:
    task = _task()
    runtime = FakeRuntime(task)
    workspace = FakeWorkspaceContainer()
    blobs = FakeBlobContainer()
    store = CosmosBlobArtifactGatewayStore(runtime, cast(Any, workspace), cast(Any, blobs))

    ref = await store.persist_bytes(task.id, ArtifactKind.DATA, "out.bin", b"payload")
    blob_name = (
        f"{task.tenant_id}/{task.owner_object_id}/{task.session_id}/{task.id}/"
        f"{ref.artifact_id}/v{ref.version}/{ref.sha256}.bin"
    )
    blobs.tamper_blob(
        blob_name,
        content=b"tampered",
        metadata={"artifactId": ref.artifact_id, "version": "1", "sha256": ref.sha256, "kind": ref.kind.value},
    )

    with pytest.raises(ValueError, match="content mismatch"):
        await store.persist_bytes(task.id, ArtifactKind.DATA, "out.bin", b"payload")


@pytest.mark.asyncio
async def test_restart_reuse_keeps_operation_result_ref_material_stable() -> None:
    task = _task()
    runtime = FakeRuntime(task)
    workspace = FakeWorkspaceContainer()
    blobs = FakeBlobContainer()
    first_store = CosmosBlobArtifactGatewayStore(runtime, cast(Any, workspace), cast(Any, blobs))
    second_store = CosmosBlobArtifactGatewayStore(runtime, cast(Any, workspace), cast(Any, blobs))

    payload = b'{"status":"ok","summary":"same"}'
    first = await first_store.persist_bytes(task.id, ArtifactKind.MANIFEST, "operation-result.json", payload)
    second = await second_store.persist_bytes(task.id, ArtifactKind.MANIFEST, "operation-result.json", payload)

    assert first == second
    assert sha256(payload).hexdigest() == first.sha256
