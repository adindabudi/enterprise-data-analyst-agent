from __future__ import annotations

import hashlib
from typing import Literal, Protocol

from azure.core import MatchConditions
from azure.core.exceptions import ResourceExistsError
from azure.cosmos.aio import ContainerProxy
from azure.cosmos.exceptions import CosmosHttpResponseError
from azure.storage.blob.aio import ContainerClient
from eda_contracts import ArtifactKind, ArtifactRef
from eda_runtime_state.models import TaskRecord
from pydantic import BaseModel, ConfigDict, Field

from .models import UploadRecord, camel_case
from .query_results import artifact_id, blob_name


class InputArtifactWriter(Protocol):
    async def write(self, task: TaskRecord, upload: UploadRecord, content: bytes) -> ArtifactRef: ...


class InputArtifactMetadata(BaseModel):
    model_config = ConfigDict(alias_generator=camel_case, populate_by_name=True, extra="forbid", frozen=True)

    id: str
    record_type: Literal["artifactGateway"] = "artifactGateway"
    tenant_id: str
    owner_object_id: str
    session_id: str
    task_id: str
    artifact_id: str
    version: int = Field(ge=1)
    kind: Literal[ArtifactKind.INPUT] = ArtifactKind.INPUT
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    display_name: str = Field(min_length=1, max_length=255)
    blob_name: str = Field(min_length=1, max_length=1024)
    size_bytes: int = Field(ge=0, le=52_428_800)


class CosmosBlobInputArtifactWriter:
    def __init__(self, workspace: ContainerProxy, session_blobs: ContainerClient) -> None:
        self._workspace = workspace
        self._session_blobs = session_blobs

    async def write(self, task: TaskRecord, upload: UploadRecord, content: bytes) -> ArtifactRef:
        if (
            upload.tenant_id != task.tenant_id
            or upload.owner_object_id != task.owner_object_id
            or upload.session_id != task.session_id
            or upload.state != "clean"
            or not upload.blob_etag
            or len(content) != upload.size_bytes
            or hashlib.sha256(content).hexdigest() != upload.sha256
        ):
            raise ValueError("input does not match a verified upload in task scope")
        ref = ArtifactRef(
            artifact_id=artifact_id(
                task,
                kind=ArtifactKind.INPUT,
                display_name=f"{upload.id}:{upload.display_name}",
                digest=upload.sha256,
            ),
            version=1,
            kind=ArtifactKind.INPUT,
            sha256=upload.sha256,
        )
        metadata = InputArtifactMetadata(
            id=f"artifact::{ref.artifact_id}::v{ref.version}",
            tenant_id=str(task.tenant_id),
            owner_object_id=str(task.owner_object_id),
            session_id=task.session_id,
            task_id=task.id,
            artifact_id=ref.artifact_id,
            version=ref.version,
            sha256=ref.sha256,
            display_name=upload.display_name,
            blob_name=blob_name(task, ref),
            size_bytes=upload.size_bytes,
        )
        await self._upload(metadata, content)
        body = metadata.model_dump(mode="json", by_alias=True)
        try:
            await self._workspace.create_item(body)
        except CosmosHttpResponseError as error:
            if error.status_code != 409:
                raise
            existing = await self._workspace.read_item(item=metadata.id, partition_key=task.partition().values())
            stored = InputArtifactMetadata.model_validate(
                {key: value for key, value in existing.items() if not key.startswith("_")}
            )
            if stored != metadata:
                raise ValueError("input artifact metadata conflict") from error
        return ref

    async def _upload(self, metadata: InputArtifactMetadata, content: bytes) -> None:
        blob = self._session_blobs.get_blob_client(metadata.blob_name)
        expected = {
            "artifactId": metadata.artifact_id,
            "version": str(metadata.version),
            "sha256": metadata.sha256,
            "kind": "input",
        }
        try:
            await blob.upload_blob(content, overwrite=False, metadata=expected)
        except ResourceExistsError:
            properties = await blob.get_blob_properties()
            normalized = {key.lower(): value for key, value in properties.metadata.items()}
            if properties.size != metadata.size_bytes or normalized != {
                key.lower(): value for key, value in expected.items()
            }:
                raise ValueError("existing input blob metadata mismatch") from None
            stream = await blob.download_blob(
                etag=properties.etag,
                match_condition=MatchConditions.IfNotModified,
                max_concurrency=1,
                decompress=False,
            )
            existing = await stream.readall()
            if len(existing) != metadata.size_bytes or hashlib.sha256(existing).hexdigest() != metadata.sha256:
                raise ValueError("existing input blob digest mismatch") from None