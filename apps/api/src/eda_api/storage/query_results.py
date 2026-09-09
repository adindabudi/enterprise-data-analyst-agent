"""Write one query result into the task's artifact store so the sandbox can read it.

`execute_in_sandbox` only accepts artifact references, and the durable worker
cannot reach Fabric, so a result the chat already holds has no other way through.
Passing it as handoff text does not work: the graph tool truncates rows at eight
thousand characters and the handoff itself is capped at twelve thousand, while a
modest table measured 13,741.

The record shape here matches the worker gateway exactly, because the worker
reads it back by artifact reference and verifies the digest against both the
metadata and the reference. What is written is a *candidate*: it carries no
`sourceVersion`, so it can never be mistaken for a validated, published artifact.
"""

from __future__ import annotations

import hashlib
from typing import Any, Protocol

from azure.core.exceptions import ResourceExistsError
from azure.cosmos.aio import ContainerProxy
from azure.cosmos.exceptions import CosmosHttpResponseError
from azure.storage.blob.aio import ContainerClient
from eda_contracts import ArtifactKind, ArtifactRef
from eda_runtime_state.models import TaskRecord
from pydantic import BaseModel, ConfigDict, Field

from .models import camel_case

MAX_QUERY_RESULT_BYTES = 8 * 1024 * 1024


class QueryResultWriter(Protocol):
    async def write(self, task: TaskRecord, display_name: str, content: bytes) -> ArtifactRef: ...


class _CandidateMetadata(BaseModel):
    model_config = ConfigDict(alias_generator=camel_case, populate_by_name=True, extra="forbid", frozen=True)

    id: str
    record_type: str = Field(default="artifactGateway")
    tenant_id: str
    owner_object_id: str
    session_id: str
    task_id: str
    artifact_id: str
    version: int = Field(ge=1)
    kind: ArtifactKind
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    display_name: str = Field(min_length=1, max_length=255)
    blob_name: str = Field(min_length=1, max_length=1024)
    size_bytes: int = Field(ge=0)
    validation_candidate_artifact_id: str | None = None
    validation_candidate_version: int | None = None
    validation_candidate_sha256: str | None = None
    validation_profile: str | None = None
    validation_status: str | None = None
    source_version: int | None = None
    report_artifact_id: str | None = None
    report_version: int | None = None


class CosmosBlobQueryResultWriter:
    def __init__(
        self,
        workspace: ContainerProxy,
        session_blobs: ContainerClient,
        *,
        max_bytes: int = MAX_QUERY_RESULT_BYTES,
    ) -> None:
        self._workspace = workspace
        self._session_blobs = session_blobs
        self._max_bytes = max_bytes

    async def write(self, task: TaskRecord, display_name: str, content: bytes) -> ArtifactRef:
        if not content:
            raise ValueError("query result is empty")
        if len(content) > self._max_bytes:
            raise ValueError("query result exceeds the configured artifact bound")
        digest = hashlib.sha256(content).hexdigest()
        ref = ArtifactRef(
            artifact_id=artifact_id(task, kind=ArtifactKind.DATA, display_name=display_name, digest=digest),
            version=1,
            kind=ArtifactKind.DATA,
            sha256=digest,
        )
        metadata = _CandidateMetadata(
            id=f"artifact::{ref.artifact_id}::v{ref.version}",
            tenant_id=str(task.tenant_id),
            owner_object_id=str(task.owner_object_id),
            session_id=task.session_id,
            task_id=task.id,
            artifact_id=ref.artifact_id,
            version=ref.version,
            kind=ref.kind,
            sha256=ref.sha256,
            display_name=display_name,
            blob_name=blob_name(task, ref),
            size_bytes=len(content),
        )
        await self._upload(metadata.blob_name, content, ref)
        try:
            await self._workspace.create_item(metadata.model_dump(mode="json", by_alias=True))
        except CosmosHttpResponseError as error:
            # The identifier is derived from the content, so a conflict is the same result written twice.
            if error.status_code != 409:
                raise
        return ref

    async def _upload(self, name: str, content: bytes, ref: ArtifactRef) -> None:
        blob = self._session_blobs.get_blob_client(name)
        metadata = {
            "artifactId": ref.artifact_id,
            "version": str(ref.version),
            "sha256": ref.sha256,
            "kind": ref.kind.value,
        }
        try:
            await blob.upload_blob(content, overwrite=False, metadata=metadata)
        except ResourceExistsError:
            stream = await blob.download_blob(max_concurrency=1)
            existing: Any = await stream.readall()
            if hashlib.sha256(bytes(existing)).hexdigest() != ref.sha256:
                raise ValueError("stored query result does not match its digest") from None


def artifact_id(task: TaskRecord, *, kind: ArtifactKind, display_name: str, digest: str) -> str:
    material = "\0".join(
        [
            str(task.tenant_id),
            str(task.owner_object_id),
            task.session_id,
            task.id,
            kind.value,
            display_name,
            digest,
        ]
    ).encode("utf-8")
    return f"artifact-{hashlib.sha256(material).hexdigest()[:40]}"


def blob_name(task: TaskRecord, ref: ArtifactRef) -> str:
    return (
        f"{task.tenant_id}/{task.owner_object_id}/{task.session_id}/{task.id}/"
        f"{ref.artifact_id}/v{ref.version}/{ref.sha256}.bin"
    )
