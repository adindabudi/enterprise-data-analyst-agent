from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest
from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.cosmos.exceptions import CosmosHttpResponseError, CosmosResourceNotFoundError
from eda_api.storage.models import UploadRecord
from eda_contracts import ArtifactKind
from eda_runtime_state.models import TaskRecord, TaskStatus
from eda_runtime_state.tasks import InMemoryRuntimeStateRepository
from eda_worker.sandbox.gateway import CosmosBlobArtifactGatewayStore

CONTENT = b"category,value\nrevenue,42\n"
NOW = datetime.now(UTC)
TASK = TaskRecord(
    id="task_uploaded_inputs01",
    tenant_id=UUID(int=1),
    owner_object_id=UUID(int=2),
    session_id="ses_uploaded_inputs01",
    status=TaskStatus.PLANNING,
    checkpoint_sequence=0,
    command_sequence=0,
    applied_command_sequence=0,
    created_at=NOW,
    updated_at=NOW,
    expires_at=NOW + timedelta(days=1),
)
UPLOAD = UploadRecord(
    id="upl_verified_input01",
    tenant_id=TASK.tenant_id,
    owner_object_id=TASK.owner_object_id,
    session_id=TASK.session_id,
    blob_name=f"quarantine/{TASK.tenant_id}/{TASK.owner_object_id}/upl_verified_input01",
    blob_etag="quarantine-etag-01",
    display_name="revenue.csv",
    sha256=hashlib.sha256(CONTENT).hexdigest(),
    size_bytes=len(CONTENT),
    state="clean",
    created_at=NOW,
)


class Blob:
    def __init__(self) -> None:
        self.content: bytes | None = None
        self.metadata: dict[str, str] = {}
        self.downloads = 0

    async def upload_blob(self, content: bytes, *, overwrite: bool, metadata: dict[str, str]) -> None:
        assert overwrite is False
        if self.content is not None:
            raise ResourceExistsError("exists")
        self.content, self.metadata = bytes(content), dict(metadata)

    async def get_blob_properties(self, **kwargs: Any) -> SimpleNamespace:
        del kwargs
        if self.content is None:
            raise ResourceNotFoundError("missing")
        return SimpleNamespace(size=len(self.content), metadata=self.metadata.copy(), etag="artifact-etag")

    async def download_blob(self, **kwargs: Any) -> Blob:
        del kwargs
        self.downloads += 1
        return self

    async def readall(self) -> bytes:
        assert self.content is not None
        return self.content

    async def chunks(self) -> AsyncIterator[bytes]:
        yield await self.readall()


class Blobs:
    def __init__(self) -> None:
        self.items: dict[str, Blob] = {}

    def get_blob_client(self, name: str) -> Blob:
        return self.items.setdefault(name, Blob())


class Workspace:
    def __init__(self) -> None:
        self.items: dict[tuple[str, ...], dict[str, Any]] = {}
        self.fail_next_write = False

    async def create_item(self, body: dict[str, Any]) -> dict[str, Any]:
        if self.fail_next_write:
            self.fail_next_write = False
            raise CosmosHttpResponseError(status_code=503, message="temporary failure")
        key = tuple(str(body[field]) for field in ("tenantId", "ownerObjectId", "sessionId", "id"))
        if key in self.items:
            raise CosmosHttpResponseError(status_code=409, message="exists")
        self.items[key] = dict(body)
        return dict(body)

    async def read_item(self, item: str, partition_key: list[str]) -> dict[str, Any]:
        try:
            return {**self.items[(*partition_key, item)], "_etag": "cosmos-etag"}
        except KeyError as error:
            raise CosmosResourceNotFoundError(status_code=404, message="missing") from error

    def query_items(self, *, query: str, parameters: list[dict[str, Any]], partition_key: list[str]) -> AsyncIterator[dict[str, Any]]:
        task_id = next(parameter["value"] for parameter in parameters if parameter["name"] == "@taskId")

        async def records() -> AsyncIterator[dict[str, Any]]:
            for key, item in self.items.items():
                if key[:3] != tuple(partition_key) or item.get("taskId") != task_id:
                    continue
                if "IS_DEFINED(c.sourceVersion)" in query and "sourceVersion" not in item:
                    continue
                if "IS_NUMBER(c.sourceVersion)" in query and not isinstance(item.get("sourceVersion"), int):
                    continue
                if "c.kind != 'input'" in query and item.get("kind") == "input":
                    continue
                if "@validationProfile" in query:
                    profile = next(parameter["value"] for parameter in parameters if parameter["name"] == "@validationProfile")
                    if item.get("validationProfile") != profile:
                        continue
                if query.startswith("SELECT c.artifactId, c.version, c.kind, c.sha256 FROM c "):
                    yield {field: item[field] for field in ("artifactId", "version", "kind", "sha256")}
                else:
                    yield dict(item)

        return records()


def writer(workspace: Workspace, blobs: Blobs):
    from eda_api.storage.inputs import CosmosBlobInputArtifactWriter

    return CosmosBlobInputArtifactWriter(workspace, blobs)


@pytest.mark.asyncio
async def test_clean_input_uses_real_gateway_metadata_without_publication() -> None:
    workspace, blobs = Workspace(), Blobs()

    ref = await writer(workspace, blobs).write(TASK, UPLOAD, CONTENT)

    metadata = next(iter(workspace.items.values()))
    assert ref.kind is ArtifactKind.INPUT
    assert ref.version == 1
    assert metadata == {
        "id": f"artifact::{ref.artifact_id}::v1",
        "recordType": "artifactGateway",
        "tenantId": str(TASK.tenant_id),
        "ownerObjectId": str(TASK.owner_object_id),
        "sessionId": TASK.session_id,
        "taskId": TASK.id,
        "artifactId": ref.artifact_id,
        "version": 1,
        "kind": "input",
        "sha256": UPLOAD.sha256,
        "displayName": UPLOAD.display_name,
        "sizeBytes": len(CONTENT),
        "blobName": f"{TASK.tenant_id}/{TASK.owner_object_id}/{TASK.session_id}/{TASK.id}/{ref.artifact_id}/v1/{ref.sha256}.bin",
    }
    runtime = InMemoryRuntimeStateRepository()
    await runtime.create_task(TASK.model_copy(update={"input_artifacts": (ref,)}), "input-task-key")
    gateway = CosmosBlobArtifactGatewayStore(runtime, workspace, blobs)
    assert await gateway.read_bytes(TASK.id, ref) == CONTENT
    assert await gateway.published_refs(TASK.id) == ()


@pytest.mark.asyncio
async def test_input_write_is_idempotent_after_partial_storage_failure() -> None:
    workspace, blobs = Workspace(), Blobs()
    inputs = writer(workspace, blobs)
    workspace.fail_next_write = True

    with pytest.raises(CosmosHttpResponseError):
        await inputs.write(TASK, UPLOAD, CONTENT)
    first = await inputs.write(TASK, UPLOAD, CONTENT)
    retried = await inputs.write(TASK, UPLOAD, CONTENT)

    assert first == retried
    assert len(blobs.items) == len(workspace.items) == 1


@pytest.mark.parametrize("field", ["taskId", "sha256", "blobName", "kind"])
@pytest.mark.asyncio
async def test_input_write_rejects_conflicting_catalog_metadata(field: str) -> None:
    workspace, blobs = Workspace(), Blobs()
    inputs = writer(workspace, blobs)
    await inputs.write(TASK, UPLOAD, CONTENT)
    next(iter(workspace.items.values()))[field] = "different"

    with pytest.raises(ValueError):
        await inputs.write(TASK, UPLOAD, CONTENT)


@pytest.mark.parametrize("corrupt", ["content", "metadata"])
@pytest.mark.asyncio
async def test_input_retry_rechecks_existing_blob(corrupt: str) -> None:
    workspace, blobs = Workspace(), Blobs()
    inputs = writer(workspace, blobs)
    await inputs.write(TASK, UPLOAD, CONTENT)
    blob = next(iter(blobs.items.values()))
    if corrupt == "content":
        blob.content = CONTENT.replace(b"42", b"99")
    else:
        blob.metadata["kind"] = "data"

    with pytest.raises(ValueError):
        await inputs.write(TASK, UPLOAD, CONTENT)


@pytest.mark.parametrize("field", ["tenant_id", "owner_object_id", "session_id", "state", "sha256", "size_bytes"])
@pytest.mark.asyncio
async def test_input_writer_rejects_unverified_or_foreign_content(field: str) -> None:
    workspace, blobs = Workspace(), Blobs()
    changes: dict[str, Any] = {
        "tenant_id": UUID(int=8),
        "owner_object_id": UUID(int=9),
        "session_id": "ses_different_session",
        "state": "scanning",
        "sha256": "0" * 64,
        "size_bytes": 999,
    }
    record = UPLOAD.model_copy(update={field: changes[field]})

    with pytest.raises(ValueError):
        await writer(workspace, blobs).write(TASK, record, CONTENT)

    assert workspace.items == {}
    assert blobs.items == {}


@pytest.mark.asyncio
async def test_gateway_does_not_read_an_input_missing_from_canonical_task() -> None:
    workspace, blobs = Workspace(), Blobs()
    ref = await writer(workspace, blobs).write(TASK, UPLOAD, CONTENT)
    runtime = InMemoryRuntimeStateRepository()
    await runtime.create_task(TASK, "unbound-input-task")
    gateway = CosmosBlobArtifactGatewayStore(runtime, workspace, blobs)

    with pytest.raises(ValueError, match="scope"):
        await gateway.read_bytes(TASK.id, ref)

    assert next(iter(blobs.items.values())).downloads == 0


@pytest.mark.asyncio
async def test_unbound_input_never_reaches_sandbox_execution() -> None:
    from eda_worker.sandbox.gateway import DynamicSessionCapabilityGateway
    from eda_worker.tools.contracts import CapabilityStatus, ExecuteSandboxOperation, SandboxRuntime

    from services.worker.tests.sandbox.test_gateway import FakeClient

    workspace, blobs = Workspace(), Blobs()
    ref = await writer(workspace, blobs).write(TASK, UPLOAD, CONTENT)
    runtime = InMemoryRuntimeStateRepository()
    await runtime.create_task(TASK, "unbound-input-execute")
    store = CosmosBlobArtifactGatewayStore(runtime, workspace, blobs)
    client = FakeClient()
    gateway = DynamicSessionCapabilityGateway(client, runtime, store)

    result = await gateway.execute(
        TASK.id, ExecuteSandboxOperation(runtime=SandboxRuntime.PYTHON, source="print('data')", input_artifacts=(ref,))
    )

    assert result.status is CapabilityStatus.BLOCKED
    assert client.import_calls == client.execution_calls == 0


@pytest.mark.asyncio
async def test_clean_upload_flows_through_task_context_into_sandbox_as_an_input() -> None:
    from eda_worker.context.repository import RuntimeTaskStateRepository
    from eda_worker.sandbox.gateway import DynamicSessionCapabilityGateway
    from eda_worker.tools.contracts import CapabilityStatus, ExecuteSandboxOperation, SandboxRuntime

    from apps.api.tests.unit.test_task_uploads import Pipeline
    from services.worker.tests.sandbox.test_gateway import FakeClient

    pipeline = Pipeline()
    partition, message_id, upload_id = await pipeline.source()
    task = await pipeline.service.start_task(partition, "sandbox-upload-key", message_id, input_upload_ids=(upload_id,))

    class Messages:
        async def load_canonical(self, message_partition, message_ids):
            assert message_partition.values() == task.partition().values()
            return [await pipeline.messages.get_owned(task.partition(), identifier) for identifier in message_ids]

    class RecordingClient(FakeClient):
        def __init__(self) -> None:
            super().__init__()
            self.inputs: list[tuple[str, bytes]] = []

        async def import_bytes(self, identifier: str, category: str, display_name: str, body: bytes):
            if category == "input":
                self.inputs.append((display_name, body))
            return await super().import_bytes(identifier, category, display_name, body)

    snapshot = await RuntimeTaskStateRepository(pipeline.runtime, Messages()).context_snapshot(task.id)
    store = CosmosBlobArtifactGatewayStore(pipeline.runtime, pipeline.workspace, pipeline.blobs)
    client = RecordingClient()
    gateway = DynamicSessionCapabilityGateway(client, pipeline.runtime, store)

    result = await gateway.execute(
        task.id,
        ExecuteSandboxOperation(
            runtime=SandboxRuntime.PYTHON,
            source="from pathlib import Path\nprint(next(Path('../inputs').glob('*.csv')).read_text())",
            input_artifacts=snapshot.input_artifacts,
        ),
    )

    assert result.status is CapabilityStatus.OK
    assert client.inputs == [(f"{task.input_artifacts[0].artifact_id}.csv", b"value\n42\n")]
    assert all(ref.kind is not ArtifactKind.INPUT for ref in result.artifact_refs)
    assert snapshot.query_results == ()


@pytest.mark.asyncio
async def test_uploaded_input_cannot_be_promoted_by_a_passing_output_validator() -> None:
    from eda_worker.tools.contracts import ValidationProfile

    workspace, blobs = Workspace(), Blobs()
    ref = await writer(workspace, blobs).write(TASK, UPLOAD, CONTENT)
    runtime = InMemoryRuntimeStateRepository()
    await runtime.create_task(TASK.model_copy(update={"input_artifacts": (ref,)}), "read-only-input-task")
    store = CosmosBlobArtifactGatewayStore(runtime, workspace, blobs)
    report = await store.persist_validation(TASK.id, ref, ValidationProfile.CORE_HTML, b'{"status":"passed"}', "passed")

    with pytest.raises(ValueError, match="input"):
        await store.publish(TASK.id, ref, report)

    assert await store.published_refs(TASK.id) == ()


@pytest.mark.parametrize("field", ["tenantId", "ownerObjectId", "sessionId", "blobName"])
@pytest.mark.asyncio
async def test_gateway_checks_promoted_input_metadata_scope(field: str) -> None:
    workspace, blobs = Workspace(), Blobs()
    ref = await writer(workspace, blobs).write(TASK, UPLOAD, CONTENT)
    runtime = InMemoryRuntimeStateRepository()
    await runtime.create_task(TASK.model_copy(update={"input_artifacts": (ref,)}), "input-metadata-task")
    metadata = next(iter(workspace.items.values()))
    stored_metadata = metadata.copy()
    metadata[field] = "foreign-scope"
    workspace.items[(*TASK.partition().values(), metadata["id"])] = metadata
    store = CosmosBlobArtifactGatewayStore(runtime, workspace, blobs)

    with pytest.raises(ValueError, match="scope"):
        await store.read_bytes(TASK.id, ref)

    assert set(blobs.items) == {stored_metadata["blobName"]}


@pytest.mark.asyncio
async def test_gateway_accepts_inputs_above_the_old_25_mib_bound() -> None:
    content = b"0" * (26 * 1024 * 1024)
    upload = UPLOAD.model_copy(update={"size_bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()})
    workspace, blobs = Workspace(), Blobs()
    ref = await writer(workspace, blobs).write(TASK, upload, content)
    runtime = InMemoryRuntimeStateRepository()
    await runtime.create_task(TASK.model_copy(update={"input_artifacts": (ref,)}), "large-input-task")
    store = CosmosBlobArtifactGatewayStore(runtime, workspace, blobs)

    assert await store.read_bytes(TASK.id, ref) == content


@pytest.mark.asyncio
async def test_null_source_version_is_not_a_published_output() -> None:
    from eda_api.storage.artifacts import CosmosBlobArtifactCatalog

    workspace, blobs = Workspace(), Blobs()
    runtime = InMemoryRuntimeStateRepository()
    await runtime.create_task(TASK, "candidate-not-published")
    store = CosmosBlobArtifactGatewayStore(runtime, workspace, blobs)
    await store.persist_bytes(TASK.id, ArtifactKind.HTML, "draft.html", b"<p>draft</p>")

    assert await store.published_refs(TASK.id) == ()
    assert await CosmosBlobArtifactCatalog(workspace, blobs).list_published(TASK) == ()


@pytest.mark.asyncio
async def test_input_kind_cannot_be_spoofed_as_generated_data() -> None:
    from eda_worker.sandbox.gateway import InMemoryArtifactGatewayStore

    store = InMemoryArtifactGatewayStore()
    ref = await store.persist_bytes(TASK.id, ArtifactKind.INPUT, "data.csv", CONTENT)
    spoofed = ref.model_copy(update={"kind": ArtifactKind.DATA})

    with pytest.raises(ValueError, match="digest"):
        await store.read_bytes(TASK.id, spoofed)