from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from azure.core.exceptions import ResourceExistsError
from eda_api.storage.query_results import CosmosBlobQueryResultWriter, artifact_id, blob_name
from eda_contracts import ArtifactKind
from eda_runtime_state.models import TaskRecord, TaskStatus

TASK = TaskRecord(
    id="task_abcdefgh",
    tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
    owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
    session_id="ses_interactive_12345678",
    status=TaskStatus.ANALYZING,
    checkpoint_sequence=0,
    command_sequence=0,
    applied_command_sequence=0,
    created_at=datetime(2026, 7, 31, tzinfo=UTC),
    updated_at=datetime(2026, 7, 31, tzinfo=UTC),
    expires_at=datetime(2026, 8, 31, tzinfo=UTC),
)
ROWS = b'[{"id":1,"total":24}]'


class Blob:
    def __init__(self, existing: bytes | None = None) -> None:
        self.uploaded: bytes | None = None
        self.metadata: dict[str, str] = {}
        self._existing = existing

    async def upload_blob(self, content: bytes, *, overwrite: bool, metadata: dict[str, str]) -> None:
        assert overwrite is False
        if self._existing is not None:
            raise ResourceExistsError("already there")
        self.uploaded = bytes(content)
        self.metadata = dict(metadata)

    async def download_blob(self, **kwargs: Any) -> Any:
        del kwargs

        class Stream:
            def __init__(self, payload: bytes) -> None:
                self._payload = payload

            async def readall(self) -> bytes:
                return self._payload

        return Stream(self._existing or b"")


class Blobs:
    def __init__(self, existing: bytes | None = None) -> None:
        self.blob = Blob(existing)
        self.requested: list[str] = []

    def get_blob_client(self, name: str) -> Blob:
        self.requested.append(name)
        return self.blob


class Workspace:
    def __init__(self, conflict: int | None = None) -> None:
        self.items: list[dict[str, Any]] = []
        self._conflict = conflict

    async def create_item(self, body: dict[str, Any]) -> None:
        if self._conflict is not None:
            from azure.cosmos.exceptions import CosmosHttpResponseError

            raise CosmosHttpResponseError(status_code=self._conflict, message="conflict")
        self.items.append(body)


@pytest.mark.asyncio
async def test_the_writer_records_a_candidate_that_can_never_read_as_published() -> None:
    workspace, blobs = Workspace(), Blobs()
    writer = CosmosBlobQueryResultWriter(workspace, blobs)  # type: ignore[arg-type]

    ref = await writer.write(TASK, "fabric-query-result.json", ROWS)

    record = workspace.items[0]
    # list_published filters on IS_DEFINED(c.sourceVersion); a candidate must never satisfy it.
    assert record["sourceVersion"] is None
    assert record["validationStatus"] is None
    assert record["recordType"] == "artifactGateway"
    assert ref.kind is ArtifactKind.DATA


@pytest.mark.asyncio
async def test_the_record_matches_the_shape_the_worker_reads_back() -> None:
    workspace, blobs = Workspace(), Blobs()
    writer = CosmosBlobQueryResultWriter(workspace, blobs)  # type: ignore[arg-type]

    ref = await writer.write(TASK, "fabric-query-result.json", ROWS)

    record = workspace.items[0]
    digest = hashlib.sha256(ROWS).hexdigest()
    # The worker resolves by (taskId, ref) and rejects any digest that disagrees with either side.
    assert ref.sha256 == digest == record["sha256"]
    assert record["id"] == f"artifact::{ref.artifact_id}::v1"
    assert record["blobName"] == blob_name(TASK, ref)
    assert blobs.requested == [record["blobName"]]
    assert blobs.blob.metadata == {
        "artifactId": ref.artifact_id,
        "version": "1",
        "sha256": digest,
        "kind": "data",
    }


def test_the_identifier_is_derived_the_same_way_the_worker_derives_it() -> None:
    digest = hashlib.sha256(ROWS).hexdigest()
    material = "\0".join(
        [
            str(TASK.tenant_id),
            str(TASK.owner_object_id),
            TASK.session_id,
            TASK.id,
            "data",
            "fabric-query-result.json",
            digest,
        ]
    ).encode("utf-8")

    expected = f"artifact-{hashlib.sha256(material).hexdigest()[:40]}"

    assert artifact_id(TASK, kind=ArtifactKind.DATA, display_name="fabric-query-result.json", digest=digest) == expected


@pytest.mark.asyncio
async def test_writing_the_same_rows_twice_is_the_same_artifact() -> None:
    workspace, blobs = Workspace(conflict=409), Blobs(existing=ROWS)
    writer = CosmosBlobQueryResultWriter(workspace, blobs)  # type: ignore[arg-type]

    first = await writer.write(TASK, "fabric-query-result.json", ROWS)
    second = await writer.write(TASK, "fabric-query-result.json", ROWS)

    assert first == second


@pytest.mark.asyncio
async def test_a_stored_result_that_does_not_match_its_digest_is_refused() -> None:
    workspace, blobs = Workspace(), Blobs(existing=b"something else entirely")
    writer = CosmosBlobQueryResultWriter(workspace, blobs)  # type: ignore[arg-type]

    with pytest.raises(ValueError):
        await writer.write(TASK, "fabric-query-result.json", ROWS)


@pytest.mark.parametrize(("content", "reason"), [(b"", "empty"), (b"x" * 40, "too large")])
@pytest.mark.asyncio
async def test_the_writer_refuses_content_it_should_not_store(content: bytes, reason: str) -> None:
    del reason
    workspace, blobs = Workspace(), Blobs()
    writer = CosmosBlobQueryResultWriter(workspace, blobs, max_bytes=32)  # type: ignore[arg-type]

    with pytest.raises(ValueError):
        await writer.write(TASK, "fabric-query-result.json", content)

    assert workspace.items == []


@pytest.mark.asyncio
async def test_a_cosmos_failure_that_is_not_a_conflict_still_surfaces() -> None:
    workspace, blobs = Workspace(conflict=503), Blobs()
    writer = CosmosBlobQueryResultWriter(workspace, blobs)  # type: ignore[arg-type]

    from azure.cosmos.exceptions import CosmosHttpResponseError

    with pytest.raises(CosmosHttpResponseError):
        await writer.write(TASK, "fabric-query-result.json", ROWS)
