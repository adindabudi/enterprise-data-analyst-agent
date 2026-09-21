from __future__ import annotations

import hashlib
from typing import Any, Protocol, cast

from azure.core import MatchConditions
from azure.core.exceptions import HttpResponseError
from azure.cosmos.aio import ContainerProxy
from azure.cosmos.exceptions import CosmosResourceNotFoundError
from azure.storage.blob.aio import ContainerClient
from eda_contracts import ArtifactKind
from eda_runtime_state.models import TaskRecord
from pydantic import BaseModel, ConfigDict, Field

from .inputs import InputArtifactMetadata
from .models import camel_case
from .query_results import blob_name

MAX_ARTIFACT_READ_BYTES = 64 * 1024 * 1024


class PublishedArtifact(BaseModel):
    model_config = ConfigDict(alias_generator=camel_case, populate_by_name=True, extra="ignore", frozen=True)

    artifact_id: str = Field(min_length=1, max_length=128)
    version: int = Field(ge=1)
    kind: ArtifactKind
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    display_name: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(ge=0)


class ArtifactCatalog(Protocol):
    async def list_published(self, task: TaskRecord) -> tuple[PublishedArtifact, ...]: ...

    async def list_inputs(self, task: TaskRecord) -> tuple[PublishedArtifact, ...]: ...

    async def read_content(
        self, task: TaskRecord, artifact_id: str, version: int
    ) -> tuple[PublishedArtifact, bytes]: ...


class InMemoryArtifactCatalog:
    def __init__(self) -> None:
        self._artifacts: dict[tuple[str, str, int], tuple[PublishedArtifact, bytes]] = {}

    def add(self, task_id: str, artifact: PublishedArtifact, content: bytes) -> None:
        self._artifacts[(task_id, artifact.artifact_id, artifact.version)] = (artifact, bytes(content))

    async def list_published(self, task: TaskRecord) -> tuple[PublishedArtifact, ...]:
        return tuple(
            sorted(
                (
                    artifact
                    for (task_id, _, _), (artifact, _) in self._artifacts.items()
                    if task_id == task.id and artifact.kind is not ArtifactKind.INPUT
                ),
                key=lambda artifact: (artifact.display_name, artifact.artifact_id, artifact.version),
            )
        )

    async def list_inputs(self, task: TaskRecord) -> tuple[PublishedArtifact, ...]:
        inputs: list[PublishedArtifact] = []
        for ref in task.input_artifacts:
            artifact, _ = await self.read_content(task, ref.artifact_id, ref.version)
            inputs.append(artifact)
        return tuple(inputs)

    async def read_content(self, task: TaskRecord, artifact_id: str, version: int) -> tuple[PublishedArtifact, bytes]:
        entry = self._artifacts.get((task.id, artifact_id, version))
        if entry is None:
            raise LookupError("artifact is unavailable in task scope")
        artifact, content = entry
        if artifact.kind is ArtifactKind.INPUT and (
            not any(
                ref.artifact_id == artifact.artifact_id
                and ref.version == artifact.version
                and ref.sha256 == artifact.sha256
                for ref in task.input_artifacts
            )
            or hashlib.sha256(content).hexdigest() != artifact.sha256
            or len(content) != artifact.size_bytes
        ):
            raise LookupError("input artifact is unavailable in task scope")
        return entry


class CosmosBlobArtifactCatalog:
    """Reads the immutable published artifacts the worker gateway wrote for a task."""

    def __init__(
        self,
        workspace: ContainerProxy,
        session_blobs: ContainerClient,
        *,
        max_read_bytes: int = MAX_ARTIFACT_READ_BYTES,
    ) -> None:
        self._workspace = workspace
        self._session_blobs = session_blobs
        self._max_read_bytes = max_read_bytes

    async def list_published(self, task: TaskRecord) -> tuple[PublishedArtifact, ...]:
        artifacts = [artifact for artifact, _ in await self._query(task)]
        return tuple(
            sorted(artifacts, key=lambda artifact: (artifact.display_name, artifact.artifact_id, artifact.version))
        )

    async def list_inputs(self, task: TaskRecord) -> tuple[PublishedArtifact, ...]:
        return tuple(
            PublishedArtifact.model_validate(metadata.model_dump()) for metadata in await self._input_metadata(task)
        )

    async def read_content(self, task: TaskRecord, artifact_id: str, version: int) -> tuple[PublishedArtifact, bytes]:
        for metadata in await self._input_metadata(task):
            if metadata.artifact_id == artifact_id and metadata.version == version:
                return PublishedArtifact.model_validate(metadata.model_dump()), await self._download_input(metadata)
        for artifact, artifact_blob_name in await self._query(task):
            if artifact.artifact_id == artifact_id and artifact.version == version:
                if artifact.size_bytes > self._max_read_bytes:
                    raise LookupError("artifact exceeds the configured read bound")
                return artifact, await self._download(artifact_blob_name)
        raise LookupError("artifact is unavailable in task scope")

    async def _input_metadata(self, task: TaskRecord) -> list[InputArtifactMetadata]:
        inputs: list[InputArtifactMetadata] = []
        for ref in task.input_artifacts:
            try:
                document = await self._workspace.read_item(
                    item=f"artifact::{ref.artifact_id}::v{ref.version}", partition_key=task.partition().values()
                )
                metadata = InputArtifactMetadata.model_validate(
                    {key: value for key, value in document.items() if not key.startswith("_")}
                )
            except (CosmosResourceNotFoundError, ValueError) as error:
                raise LookupError("input artifact is unavailable in task scope") from error
            if (
                metadata.tenant_id != str(task.tenant_id)
                or metadata.owner_object_id != str(task.owner_object_id)
                or metadata.session_id != task.session_id
                or metadata.task_id != task.id
                or metadata.artifact_id != ref.artifact_id
                or metadata.version != ref.version
                or metadata.kind is not ref.kind
                or metadata.sha256 != ref.sha256
                or metadata.blob_name != blob_name(task, ref)
            ):
                raise LookupError("input artifact is unavailable in task scope")
            inputs.append(metadata)
        return inputs

    async def _download_input(self, metadata: InputArtifactMetadata) -> bytes:
        if metadata.size_bytes > self._max_read_bytes:
            raise LookupError("input exceeds the configured read bound")
        blob = self._session_blobs.get_blob_client(metadata.blob_name)
        try:
            properties = await blob.get_blob_properties()
            if properties.size != metadata.size_bytes:
                raise LookupError("input artifact size mismatch")
            stream = await blob.download_blob(
                etag=properties.etag,
                match_condition=MatchConditions.IfNotModified,
                max_concurrency=1,
                decompress=False,
            )
            content = await stream.readall()
        except HttpResponseError as error:
            if error.status_code in {404, 412}:
                raise LookupError("input artifact is unavailable") from error
            raise
        if len(content) != metadata.size_bytes or hashlib.sha256(content).hexdigest() != metadata.sha256:
            raise LookupError("input artifact digest mismatch")
        return content

    async def _query(self, task: TaskRecord) -> list[tuple[PublishedArtifact, str]]:
        query = (
            "SELECT c.artifactId, c.version, c.kind, c.sha256, c.displayName, c.sizeBytes, c.blobName FROM c "
            "WHERE c.recordType = 'artifactGateway' AND c.taskId = @taskId AND IS_NUMBER(c.sourceVersion) "
            "AND c.kind != 'input'"
        )
        iterator = self._workspace.query_items(
            query=query,
            parameters=[{"name": "@taskId", "value": task.id}],
            partition_key=[str(task.tenant_id), str(task.owner_object_id), task.session_id],
        )
        results: list[tuple[PublishedArtifact, str]] = []
        async for item in cast(Any, iterator):
            record = cast(dict[str, Any], item)
            blob_name = record.get("blobName")
            if not isinstance(blob_name, str) or not blob_name:
                continue
            results.append((PublishedArtifact.model_validate(record), blob_name))
        return results

    async def _download(self, blob_name: str) -> bytes:
        blob = self._session_blobs.get_blob_client(blob_name)
        stream = await blob.download_blob(max_concurrency=1)
        return await stream.readall()
