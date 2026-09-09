from __future__ import annotations

import hashlib
import json
import logging
import secrets
import time
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Literal, Protocol, cast

from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.cosmos.aio import ContainerProxy
from azure.cosmos.exceptions import CosmosHttpResponseError, CosmosResourceNotFoundError
from azure.storage.blob.aio import ContainerClient
from eda_contracts import ArtifactKind, ArtifactRef
from eda_runtime_state.models import OperationStatus, TaskRecord
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from eda_worker.documents.provenance import is_trusted_document_bundle
from eda_worker.metrics import record_session_stop
from eda_worker.tools.capabilities import CapabilityGateway
from eda_worker.tools.contracts import (
    ArtifactView,
    CapabilityResult,
    CapabilityStatus,
    ExecuteSandboxOperation,
    InspectArtifactOperation,
    PublishArtifactOperation,
    ValidateArtifactOperation,
    ValidationProfile,
)

from .client import ExecutionRecord, FileRecord, Runtime, ValidationResult
from .client import ValidationProfile as SandboxValidationProfile

LOGGER = logging.getLogger(__name__)

MAX_ARTIFACT_SIZE_BYTES = 1_073_741_824
DEFAULT_MAX_READ_BYTES = 50 * 1024 * 1024
type ArtifactValidationProfile = ValidationProfile


class SandboxExecutionClient(Protocol):
    def create_identifier(self) -> str: ...

    async def allocate(self, task_id: str, proposed_identifier: str) -> str: ...

    async def import_bytes(
        self,
        identifier: str,
        category: str,
        display_name: str,
        body: bytes,
        /,
    ) -> FileRecord: ...

    async def execute(
        self,
        identifier: str,
        runtime: Runtime,
        source_file_id: str,
        timeout_seconds: int,
        /,
        parameters: dict[str, str | int | float | bool | None] | None = None,
    ) -> ExecutionRecord: ...

    async def download_file(self, identifier: str, file_id: str, /) -> bytes: ...

    async def describe_file(self, identifier: str, file_id: str, /) -> FileRecord: ...

    async def validate(
        self,
        identifier: str,
        file_id: str,
        profile: SandboxValidationProfile,
        /,
    ) -> ValidationResult: ...

    async def stop(self, identifier: str, /) -> None: ...


def _schema_preview(content: bytes, *, max_rows: int, max_characters: int) -> str:
    try:
        decoded = content.decode("utf-8")
        parsed: object = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "schema unavailable"
    if isinstance(parsed, list):
        parsed_list = cast(list[Any], parsed)
        rows: list[Any] = parsed_list[:max_rows]
        return json.dumps(rows, ensure_ascii=True, separators=(",", ":"), sort_keys=True)[:max_characters]
    if isinstance(parsed, dict):
        parsed_dict = cast(dict[str, Any], parsed)
        keys: list[str] = sorted(parsed_dict.keys())[:max_rows]
        return json.dumps({"keys": keys}, ensure_ascii=True)[:max_characters]
    return "schema unavailable"


def _sample_preview(content: bytes, *, max_characters: int) -> str:
    return content.decode("utf-8", errors="replace")[:max_characters]


@dataclass(frozen=True)
class StoredArtifact:
    ref: ArtifactRef
    task_id: str
    display_name: str
    content: bytes
    metadata: dict[str, str]


class ArtifactGatewayStore(Protocol):
    async def resolve(
        self,
        task_id: str,
        artifact: ArtifactRef,
        *,
        view: ArtifactView,
        max_rows: int,
        max_characters: int,
    ) -> str: ...

    async def read_bytes(self, task_id: str, artifact: ArtifactRef) -> bytes: ...

    async def persist_bytes(
        self, task_id: str, kind: ArtifactKind, display_name: str, content: bytes
    ) -> ArtifactRef: ...

    async def persist_validation(
        self,
        task_id: str,
        candidate: ArtifactRef,
        profile: ArtifactValidationProfile,
        report: bytes,
        status: str,
    ) -> ArtifactRef: ...

    async def publish(self, task_id: str, candidate: ArtifactRef, report: ArtifactRef) -> ArtifactRef: ...

    async def published_refs(
        self, task_id: str, *, validation_profile: str | None = None,
    ) -> tuple[ArtifactRef, ...]: ...


class RuntimeTaskReader(Protocol):
    async def resolve_task(self, task_id: str) -> TaskRecord | None: ...


class RuntimeOperationRepository(RuntimeTaskReader, Protocol):
    async def ensure_active_sandbox(self, task_id: str, proposed_identifier: str) -> str: ...

    async def clear_active_sandbox(self, task_id: str, expected_identifier: str) -> TaskRecord | None: ...

    async def begin_operation(self, task: TaskRecord, step_name: str, canonical_input: dict[str, Any]) -> Any: ...

    async def complete_operation(self, task: TaskRecord, operation_id: str, immutable_result_ref: str) -> Any: ...


class InMemoryArtifactGatewayStore:
    def __init__(self) -> None:
        self._artifacts: dict[tuple[str, str, int], StoredArtifact] = {}
        self._validation_status: dict[tuple[str, str, int], str] = {}
        self._validation_binding: dict[tuple[str, str, int], tuple[str, str, int]] = {}
        self._published: dict[tuple[str, str, int], ArtifactRef] = {}

    async def resolve(
        self,
        task_id: str,
        artifact: ArtifactRef,
        *,
        view: ArtifactView,
        max_rows: int,
        max_characters: int,
    ) -> str:
        stored = self._stored(task_id, artifact)
        if view is ArtifactView.METADATA:
            return json.dumps(
                {
                    "artifactId": stored.ref.artifact_id,
                    "version": stored.ref.version,
                    "kind": stored.ref.kind.value,
                    "sha256": stored.ref.sha256,
                    "sizeBytes": len(stored.content),
                    "displayName": stored.display_name,
                },
                ensure_ascii=True,
                sort_keys=True,
            )
        if view is ArtifactView.SCHEMA:
            return _schema_preview(stored.content, max_rows=max_rows, max_characters=max_characters)
        return _sample_preview(stored.content, max_characters=max_characters)

    async def read_bytes(self, task_id: str, artifact: ArtifactRef) -> bytes:
        return self._stored(task_id, artifact).content

    async def persist_bytes(self, task_id: str, kind: ArtifactKind, display_name: str, content: bytes) -> ArtifactRef:
        ref = ArtifactRef(
            artifact_id=f"artifact-{secrets.token_urlsafe(16)}",
            version=1,
            kind=kind,
            sha256=self._sha256(content),
        )
        key = (task_id, ref.artifact_id, ref.version)
        self._artifacts[key] = StoredArtifact(
            ref=ref,
            task_id=task_id,
            display_name=display_name,
            content=bytes(content),
            metadata={},
        )
        return ref

    async def persist_validation(
        self,
        task_id: str,
        candidate: ArtifactRef,
        profile: ArtifactValidationProfile,
        report: bytes,
        status: str,
    ) -> ArtifactRef:
        self._stored(task_id, candidate)
        profile_name = profile.value
        report_ref = await self.persist_bytes(
            task_id,
            ArtifactKind.MANIFEST,
            f"validation-{profile_name}.json",
            report,
        )
        report_key = (task_id, report_ref.artifact_id, report_ref.version)
        candidate_key = (task_id, candidate.artifact_id, candidate.version)
        self._validation_status[report_key] = status
        self._validation_binding[candidate_key] = report_key
        self._stored(task_id, report_ref).metadata["validationProfile"] = profile_name
        return report_ref

    async def publish(self, task_id: str, candidate: ArtifactRef, report: ArtifactRef) -> ArtifactRef:
        if candidate.kind is ArtifactKind.INPUT:
            raise ValueError("uploaded inputs cannot be published")
        candidate_stored = self._stored(task_id, candidate)
        report_stored = self._stored(task_id, report)
        candidate_key = (task_id, candidate.artifact_id, candidate.version)
        report_key = (task_id, report.artifact_id, report.version)
        bound = self._validation_binding.get(candidate_key)
        status = self._validation_status.get(report_key)
        if bound != report_key or status != "passed":
            raise ValueError("validation report does not bind a passing result to the candidate")
        existing = self._published.get(candidate_key)
        if existing is not None:
            return existing
        published = ArtifactRef(
            artifact_id=candidate.artifact_id,
            version=candidate.version + 1,
            kind=candidate.kind,
            sha256=candidate.sha256,
        )
        self._artifacts[(task_id, published.artifact_id, published.version)] = StoredArtifact(
            ref=published,
            task_id=task_id,
            display_name=candidate_stored.display_name,
            content=candidate_stored.content,
            metadata={
                "sourceVersion": str(candidate.version),
                "report": report_stored.ref.artifact_id,
                "validationProfile": report_stored.metadata["validationProfile"],
            },
        )
        self._published[candidate_key] = published
        return published

    async def published_refs(
        self, task_id: str, *, validation_profile: str | None = None,
    ) -> tuple[ArtifactRef, ...]:
        return tuple(
            sorted(
                (
                    ref for (owner_task_id, _, _), ref in self._published.items()
                    if owner_task_id == task_id and ref.kind is not ArtifactKind.INPUT and (
                        validation_profile is None
                        or self._stored(task_id, ref).metadata.get("validationProfile") == validation_profile
                    )
                ),
                key=lambda ref: (ref.artifact_id, ref.version),
            )
        )

    def _stored(self, task_id: str, artifact: ArtifactRef) -> StoredArtifact:
        key = (task_id, artifact.artifact_id, artifact.version)
        stored = self._artifacts.get(key)
        if stored is None:
            raise ValueError("artifact is unavailable in task scope")
        if stored.ref.sha256 != artifact.sha256 or stored.ref.kind is not artifact.kind:
            raise ValueError("artifact digest mismatch")
        return stored

    @staticmethod
    def _sha256(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()


class _ArtifactGatewayModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        serialize_by_alias=True,
    )


def _without_cosmos_fields(document: Any) -> dict[str, Any]:
    """Cosmos returns _rid, _self, _etag, _attachments and _ts on every read; the model forbids extras."""
    return {key: value for key, value in cast(dict[str, Any], document).items() if not key.startswith("_")}


class _ArtifactGatewayMetadata(_ArtifactGatewayModel):
    id: str
    record_type: Literal["artifactGateway"] = Field(default="artifactGateway", alias="recordType")
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
    size_bytes: int = Field(ge=0, le=MAX_ARTIFACT_SIZE_BYTES)
    validation_candidate_artifact_id: str | None = None
    validation_candidate_version: int | None = Field(default=None, ge=1)
    validation_candidate_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    validation_profile: str | None = None
    validation_status: str | None = None
    source_version: int | None = Field(default=None, ge=1)
    report_artifact_id: str | None = None
    report_version: int | None = Field(default=None, ge=1)


class CosmosBlobArtifactGatewayStore:
    def __init__(
        self,
        runtime: RuntimeTaskReader,
        workspace: ContainerProxy,
        session_blobs: ContainerClient,
        *,
        max_read_bytes: int = DEFAULT_MAX_READ_BYTES,
    ) -> None:
        self._runtime = runtime
        self._workspace = workspace
        self._session_blobs = session_blobs
        self._max_read_bytes = max_read_bytes

    async def resolve(
        self,
        task_id: str,
        artifact: ArtifactRef,
        *,
        view: ArtifactView,
        max_rows: int,
        max_characters: int,
    ) -> str:
        metadata = await self._load_metadata(task_id, artifact)
        if view is ArtifactView.METADATA:
            return json.dumps(
                {
                    "artifactId": metadata.artifact_id,
                    "version": metadata.version,
                    "kind": metadata.kind.value,
                    "sha256": metadata.sha256,
                    "sizeBytes": metadata.size_bytes,
                    "displayName": metadata.display_name,
                },
                ensure_ascii=True,
                sort_keys=True,
            )
        payload = await self.read_bytes(task_id, artifact)
        if view is ArtifactView.SCHEMA:
            return _schema_preview(
                payload,
                max_rows=max_rows,
                max_characters=max_characters,
            )
        return _sample_preview(payload, max_characters=max_characters)

    async def read_bytes(self, task_id: str, artifact: ArtifactRef) -> bytes:
        metadata = await self._load_metadata(task_id, artifact)
        if metadata.size_bytes > self._max_read_bytes:
            raise ValueError("artifact exceeds configured read bound")
        content = await self._download_blob(metadata.blob_name, self._max_read_bytes)
        digest = hashlib.sha256(content).hexdigest()
        if digest != metadata.sha256 or digest != artifact.sha256 or len(content) != metadata.size_bytes:
            raise ValueError("artifact digest mismatch")
        return content

    async def persist_bytes(self, task_id: str, kind: ArtifactKind, display_name: str, content: bytes) -> ArtifactRef:
        task = await self._resolve_required_task(task_id)
        digest = hashlib.sha256(content).hexdigest()
        artifact_id = self._deterministic_artifact_id(task, kind=kind, display_name=display_name, digest=digest)
        ref = ArtifactRef(artifact_id=artifact_id, version=1, kind=kind, sha256=digest)
        metadata = self._new_metadata(
            task,
            ref,
            display_name=display_name,
            blob_name=self._blob_name(task, ref),
            size_bytes=len(content),
        )
        return await self._persist_immutable(task, ref, metadata, content)

    async def persist_validation(
        self,
        task_id: str,
        candidate: ArtifactRef,
        profile: ArtifactValidationProfile,
        report: bytes,
        status: str,
    ) -> ArtifactRef:
        task = await self._resolve_required_task(task_id)
        _ = await self._read_metadata_for_task(task, candidate)
        profile_name = profile.value
        digest = hashlib.sha256(report).hexdigest()
        artifact_id = self._deterministic_artifact_id(
            task,
            kind=ArtifactKind.MANIFEST,
            display_name=f"validation-{profile_name}.json",
            digest=digest,
        )
        ref = ArtifactRef(artifact_id=artifact_id, version=1, kind=ArtifactKind.MANIFEST, sha256=digest)
        metadata = self._new_metadata(
            task,
            ref,
            display_name=f"validation-{profile_name}.json",
            blob_name=self._blob_name(task, ref),
            size_bytes=len(report),
            validation_candidate_artifact_id=candidate.artifact_id,
            validation_candidate_version=candidate.version,
            validation_candidate_sha256=candidate.sha256,
            validation_profile=profile_name,
            validation_status=status,
        )
        return await self._persist_immutable(task, ref, metadata, report)

    async def publish(self, task_id: str, candidate: ArtifactRef, report: ArtifactRef) -> ArtifactRef:
        if candidate.kind is ArtifactKind.INPUT:
            raise ValueError("uploaded inputs cannot be published")
        task = await self._resolve_required_task(task_id)
        candidate_meta = await self._read_metadata_for_task(task, candidate)
        report_meta = await self._read_metadata_for_task(task, report)
        if report_meta.validation_status != "passed":
            raise ValueError("validation report does not bind a passing result to the candidate")
        if (
            report_meta.validation_candidate_artifact_id != candidate.artifact_id
            or report_meta.validation_candidate_version != candidate.version
            or report_meta.validation_candidate_sha256 != candidate.sha256
        ):
            raise ValueError("validation report does not bind a passing result to the candidate")
        published_ref = ArtifactRef(
            artifact_id=candidate.artifact_id,
            version=candidate.version + 1,
            kind=candidate.kind,
            sha256=candidate.sha256,
        )
        source_content = await self._download_blob(candidate_meta.blob_name, MAX_ARTIFACT_SIZE_BYTES)
        digest = hashlib.sha256(source_content).hexdigest()
        if digest != candidate.sha256:
            raise ValueError("artifact digest mismatch")
        metadata = self._new_metadata(
            task,
            published_ref,
            display_name=candidate_meta.display_name,
            blob_name=self._blob_name(task, published_ref),
            size_bytes=len(source_content),
            source_version=candidate.version,
            report_artifact_id=report.artifact_id,
            report_version=report.version,
            validation_profile=report_meta.validation_profile,
        )
        return await self._persist_immutable(task, published_ref, metadata, source_content)

    async def published_refs(
        self, task_id: str, *, validation_profile: str | None = None,
    ) -> tuple[ArtifactRef, ...]:
        task = await self._resolve_required_task(task_id)
        query = (
            "SELECT c.artifactId, c.version, c.kind, c.sha256 FROM c "
            "WHERE c.recordType = 'artifactGateway' AND c.taskId = @taskId "
            "AND IS_NUMBER(c.sourceVersion) AND c.kind != 'input'"
        )
        parameters: list[dict[str, object]] = [{"name": "@taskId", "value": task_id}]
        if validation_profile is not None:
            query += " AND c.validationProfile = @validationProfile"
            parameters.append({"name": "@validationProfile", "value": validation_profile})
        iterator = self._workspace.query_items(
            query=query,
            parameters=parameters,
            partition_key=self._task_partition_key(task),
        )
        refs = [ArtifactRef.model_validate(item) async for item in cast(Any, iterator)]
        return tuple(sorted(refs, key=lambda ref: (ref.artifact_id, ref.version)))

    async def _persist_immutable(
        self,
        task: TaskRecord,
        ref: ArtifactRef,
        metadata: _ArtifactGatewayMetadata,
        content: bytes,
    ) -> ArtifactRef:
        if len(content) > MAX_ARTIFACT_SIZE_BYTES:
            raise ValueError("artifact exceeds max size")
        await self._upload_blob_immutable(metadata.blob_name, content, ref)
        body = metadata.model_dump(mode="json", by_alias=True)
        partition_key = self._task_partition_key(task)
        try:
            await self._workspace.create_item(body)
            return ref
        except CosmosHttpResponseError as error:
            if error.status_code != 409:
                raise
        existing = await self._workspace.read_item(item=metadata.id, partition_key=partition_key)
        existing_doc = _ArtifactGatewayMetadata.model_validate(_without_cosmos_fields(existing))
        if not self._metadata_equal(existing_doc, metadata):
            raise ValueError("artifact metadata conflict")
        return ref

    async def _upload_blob_immutable(self, blob_name: str, content: bytes, ref: ArtifactRef) -> None:
        blob = self._session_blobs.get_blob_client(blob_name)
        expected_metadata = {
            "artifactId": ref.artifact_id,
            "version": str(ref.version),
            "sha256": ref.sha256,
            "kind": ref.kind.value,
        }
        try:
            await blob.upload_blob(content, overwrite=False, metadata=expected_metadata)
            return
        except ResourceExistsError:
            existing = await self._download_blob(blob_name, MAX_ARTIFACT_SIZE_BYTES)
            existing_digest = hashlib.sha256(existing).hexdigest()
            if existing_digest != ref.sha256:
                raise ValueError("existing blob content mismatch") from None
            properties = await blob.get_blob_properties()
            metadata = cast(dict[str, str] | None, getattr(properties, "metadata", None)) or {}
            if metadata != expected_metadata:
                raise ValueError("existing blob metadata mismatch") from None

    async def _load_metadata(self, task_id: str, artifact: ArtifactRef) -> _ArtifactGatewayMetadata:
        task = await self._resolve_required_task(task_id)
        return await self._read_metadata_for_task(task, artifact)

    async def _read_metadata_for_task(self, task: TaskRecord, artifact: ArtifactRef) -> _ArtifactGatewayMetadata:
        if (
            artifact.kind is ArtifactKind.INPUT
            and artifact not in task.input_artifacts
            and not is_trusted_document_bundle(task.id, artifact)
        ):
            raise ValueError("input artifact is unavailable in task scope")
        partition_key = self._task_partition_key(task)
        metadata_id = self._metadata_id(artifact.artifact_id, artifact.version)
        try:
            document = await self._workspace.read_item(item=metadata_id, partition_key=partition_key)
        except CosmosResourceNotFoundError as error:
            raise ValueError("artifact is unavailable in task scope") from error
        metadata = _ArtifactGatewayMetadata.model_validate(_without_cosmos_fields(document))
        if (
            metadata.task_id != task.id
            or metadata.tenant_id != str(task.tenant_id)
            or metadata.owner_object_id != str(task.owner_object_id)
            or metadata.session_id != task.session_id
            or (artifact.kind is ArtifactKind.INPUT and metadata.blob_name != self._blob_name(task, artifact))
        ):
            raise ValueError("artifact is unavailable in task scope")
        if (
            metadata.artifact_id != artifact.artifact_id
            or metadata.version != artifact.version
            or metadata.kind is not artifact.kind
            or metadata.sha256 != artifact.sha256
        ):
            raise ValueError("artifact digest mismatch")
        if metadata.size_bytes > MAX_ARTIFACT_SIZE_BYTES:
            raise ValueError("artifact exceeds max size")
        return metadata

    async def _resolve_required_task(self, task_id: str) -> TaskRecord:
        task = await self._runtime.resolve_task(task_id)
        if task is None:
            raise ValueError("task is unavailable")
        return task

    async def _download_blob(self, blob_name: str, max_bytes: int) -> bytes:
        blob = self._session_blobs.get_blob_client(blob_name)
        try:
            properties = await blob.get_blob_properties()
        except ResourceNotFoundError as error:
            raise ValueError("artifact payload is unavailable") from error
        size = int(cast(int, getattr(properties, "size", 0)))
        if size > max_bytes:
            raise ValueError("artifact exceeds configured read bound")
        downloader = await blob.download_blob()
        content = await downloader.readall()
        if len(content) > max_bytes:
            raise ValueError("artifact exceeds configured read bound")
        return content

    @staticmethod
    def _task_partition_key(task: TaskRecord) -> list[str]:
        return [str(task.tenant_id), str(task.owner_object_id), task.session_id]

    @staticmethod
    def _metadata_id(artifact_id: str, version: int) -> str:
        return f"artifact::{artifact_id}::v{version}"

    @staticmethod
    def _metadata_equal(left: _ArtifactGatewayMetadata, right: _ArtifactGatewayMetadata) -> bool:
        return left.model_dump(mode="json", by_alias=True, exclude_none=False) == right.model_dump(
            mode="json",
            by_alias=True,
            exclude_none=False,
        )

    @staticmethod
    def _deterministic_artifact_id(task: TaskRecord, *, kind: ArtifactKind, display_name: str, digest: str) -> str:
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

    @staticmethod
    def _blob_name(task: TaskRecord, ref: ArtifactRef) -> str:
        return (
            f"{task.tenant_id}/{task.owner_object_id}/{task.session_id}/{task.id}/"
            f"{ref.artifact_id}/v{ref.version}/{ref.sha256}.bin"
        )

    def _new_metadata(
        self,
        task: TaskRecord,
        ref: ArtifactRef,
        *,
        display_name: str,
        blob_name: str,
        size_bytes: int,
        validation_candidate_artifact_id: str | None = None,
        validation_candidate_version: int | None = None,
        validation_candidate_sha256: str | None = None,
        validation_profile: str | None = None,
        validation_status: str | None = None,
        source_version: int | None = None,
        report_artifact_id: str | None = None,
        report_version: int | None = None,
    ) -> _ArtifactGatewayMetadata:
        return _ArtifactGatewayMetadata(
            id=self._metadata_id(ref.artifact_id, ref.version),
            tenant_id=str(task.tenant_id),
            owner_object_id=str(task.owner_object_id),
            session_id=task.session_id,
            task_id=task.id,
            artifact_id=ref.artifact_id,
            version=ref.version,
            kind=ref.kind,
            sha256=ref.sha256,
            display_name=display_name,
            blob_name=blob_name,
            size_bytes=size_bytes,
            validation_candidate_artifact_id=validation_candidate_artifact_id,
            validation_candidate_version=validation_candidate_version,
            validation_candidate_sha256=validation_candidate_sha256,
            validation_profile=validation_profile,
            validation_status=validation_status,
            source_version=source_version,
            report_artifact_id=report_artifact_id,
            report_version=report_version,
        )


class DynamicSessionCapabilityGateway(CapabilityGateway):
    def __init__(
        self,
        client: SandboxExecutionClient,
        runtime: RuntimeOperationRepository,
        store: ArtifactGatewayStore,
    ) -> None:
        self.client = client
        self._runtime = runtime
        self._store = store
        self._task_sessions: dict[str, str] = {}

    async def is_cancelled(self, task_id: str) -> bool:
        task = await self._runtime.resolve_task(task_id)
        return bool(task is None or task.cancellation_requested)

    async def inspect(self, task_id: str, operation: InspectArtifactOperation) -> CapabilityResult:
        if operation.artifact.kind is ArtifactKind.INPUT:
            task = await self._runtime.resolve_task(task_id)
            if task is None or (
                operation.artifact not in task.input_artifacts
                and not is_trusted_document_bundle(task_id, operation.artifact)
            ):
                return CapabilityResult(
                    status=CapabilityStatus.BLOCKED,
                    summary="Input artifact is unavailable in task scope.",
                    error_code="unbound_input_artifact",
                )
        summary = await self._store.resolve(
            task_id,
            operation.artifact,
            view=operation.view,
            max_rows=operation.max_rows,
            max_characters=operation.max_characters,
        )
        return CapabilityResult(status=CapabilityStatus.OK, summary=summary)

    async def input_filename(self, task_id: str, artifact: ArtifactRef) -> str:
        inspected = await self.inspect(
            task_id,
            InspectArtifactOperation(artifact=artifact, view=ArtifactView.METADATA, max_rows=1, max_characters=1024),
        )
        if inspected.status is not CapabilityStatus.OK:
            raise ValueError("input artifact is unavailable in task scope")
        metadata = cast(dict[str, object], json.loads(inspected.summary))
        original_name = metadata.get("displayName")
        if not isinstance(original_name, str):
            raise ValueError("input artifact name is unavailable")
        return f"{artifact.artifact_id}{PurePosixPath(original_name).suffix.lower()}"

    async def execute(self, task_id: str, operation: ExecuteSandboxOperation) -> CapabilityResult:
        if await self.is_cancelled(task_id):
            return CapabilityResult(status=CapabilityStatus.CANCELLED, summary="Task cancellation was requested.")
        task = await self._runtime.resolve_task(task_id)
        if task is None:
            return CapabilityResult(
                status=CapabilityStatus.BLOCKED, summary="Task is unavailable for sandbox execution."
            )
        if any(
            artifact.kind is ArtifactKind.INPUT
            and artifact not in task.input_artifacts
            and not is_trusted_document_bundle(task_id, artifact)
            for artifact in operation.input_artifacts
        ):
            return CapabilityResult(
                status=CapabilityStatus.BLOCKED,
                summary="Input artifact is unavailable in task scope.",
                error_code="unbound_input_artifact",
            )
        canonical_input = self._execute_canonical_input(operation)
        started = await self._runtime.begin_operation(task, "sandbox.execute", canonical_input)
        if started.status is OperationStatus.COMPLETED and started.immutable_result_ref is not None:
            return await self._load_result(task_id, started.immutable_result_ref)

        identifier = await self._session_for_task(task_id)
        source_kind = ArtifactKind.SCRIPT
        source_ref = await self._store.persist_bytes(
            task_id,
            source_kind,
            self._source_name(operation.runtime),
            operation.source.encode("utf-8"),
        )
        source_file = await self.client.import_bytes(
            identifier,
            "source",
            self._source_name(operation.runtime),
            await self._store.read_bytes(task_id, source_ref),
        )
        for artifact in operation.input_artifacts:
            payload = await self._store.read_bytes(task_id, artifact)
            display_name = await self.input_filename(task_id, artifact)
            await self.client.import_bytes(identifier, "input", display_name, payload)
        execution = await self.client.execute(
            identifier,
            Runtime(operation.runtime.value),
            source_file.file_id,
            operation.timeout_seconds,
            parameters=operation.parameters or None,
        )

        created_refs: list[ArtifactRef] = []
        output_names: set[str] = set()
        for file_id in execution.output_file_ids:
            file_record = await self.client.describe_file(identifier, file_id)
            output_names.add(file_record.display_name)
            content = await self.client.download_file(identifier, file_id)
            created_refs.append(
                await self._store.persist_bytes(
                    task_id,
                    self._artifact_kind(file_record.display_name),
                    file_record.display_name,
                    content,
                )
            )
        diagnostic_refs: list[ArtifactRef] = []
        if execution.stdout_file_id is not None:
            content = await self.client.download_file(identifier, execution.stdout_file_id)
            diagnostic_refs.append(await self._store.persist_bytes(task_id, ArtifactKind.DATA, "stdout.txt", content))
        if execution.stderr_file_id is not None:
            content = await self.client.download_file(identifier, execution.stderr_file_id)
            diagnostic_refs.append(await self._store.persist_bytes(task_id, ArtifactKind.DATA, "stderr.txt", content))

        missing_outputs = sorted(set(operation.expected_outputs) - output_names)
        if execution.status == "succeeded" and missing_outputs:
            result = CapabilityResult(
                status=CapabilityStatus.CORRECTABLE_ERROR,
                summary=f"Missing expected outputs: {', '.join(missing_outputs)}. Write files directly in outputs/.",
                artifact_refs=tuple(created_refs),
                diagnostic_refs=tuple(diagnostic_refs),
                error_code="missing_expected_outputs",
            )
        elif execution.status == "succeeded":
            result = CapabilityResult(
                status=CapabilityStatus.OK,
                summary="Sandbox execution completed.",
                artifact_refs=tuple(created_refs),
                diagnostic_refs=tuple(diagnostic_refs),
            )
        else:
            result = CapabilityResult(
                status=CapabilityStatus.CORRECTABLE_ERROR,
                summary=f"Sandbox execution ended with status {execution.status}.",
                artifact_refs=tuple(created_refs),
                diagnostic_refs=tuple(diagnostic_refs),
                error_code="sandbox_execution_failed",
                retryable=False,
            )
        immutable_ref = await self._persist_result(task_id, result)
        await self._runtime.complete_operation(task, started.id, immutable_ref)
        return result

    @staticmethod
    def _artifact_kind(display_name: str) -> ArtifactKind:
        return {
            ".docx": ArtifactKind.DOCX,
            ".html": ArtifactKind.HTML,
            ".htm": ArtifactKind.HTML,
            ".mmd": ArtifactKind.MERMAID,
            ".pdf": ArtifactKind.PDF,
            ".png": ArtifactKind.PNG,
            ".pptx": ArtifactKind.PPTX,
            ".svg": ArtifactKind.SVG,
            ".xlsm": ArtifactKind.XLSM,
            ".xlsx": ArtifactKind.XLSX,
        }.get(PurePosixPath(display_name).suffix.lower(), ArtifactKind.DATA)

    async def validate(self, task_id: str, operation: ValidateArtifactOperation) -> CapabilityResult:
        if await self.is_cancelled(task_id):
            return CapabilityResult(status=CapabilityStatus.CANCELLED, summary="Task cancellation was requested.")
        task = await self._runtime.resolve_task(task_id)
        if task is None:
            return CapabilityResult(status=CapabilityStatus.BLOCKED, summary="Task is unavailable for validation.")
        canonical_input = {
            "artifact": operation.artifact.model_dump(mode="json", by_alias=True),
            "profile": operation.profile.value,
        }
        started = await self._runtime.begin_operation(task, "sandbox.validate", canonical_input)
        if started.status is OperationStatus.COMPLETED and started.immutable_result_ref is not None:
            return await self._load_result(task_id, started.immutable_result_ref)

        sandbox_profile = self._map_profile(operation.profile)
        if sandbox_profile is None:
            result = CapabilityResult(
                status=CapabilityStatus.BLOCKED,
                summary=f"Validation profile {operation.profile.value} is not supported by this sandbox image.",
                error_code="unsupported_validation_profile",
            )
            immutable_ref = await self._persist_result(task_id, result)
            await self._runtime.complete_operation(task, started.id, immutable_ref)
            return result

        identifier = await self._session_for_task(task_id)
        payload = await self._store.read_bytes(task_id, operation.artifact)
        filename = await self.input_filename(task_id, operation.artifact)
        imported = await self.client.import_bytes(identifier, "input", filename, payload)
        validation = await self.client.validate(identifier, imported.file_id, sandbox_profile)
        report_bytes = await self.client.download_file(identifier, validation.report.file_id)
        report_ref = await self._store.persist_validation(
            task_id,
            operation.artifact,
            operation.profile,
            report_bytes,
            validation.status.value,
        )
        status = CapabilityStatus.OK if validation.status.value == "passed" else CapabilityStatus.CORRECTABLE_ERROR
        result = CapabilityResult(
            status=status,
            summary=f"Validation {validation.status.value} for profile {operation.profile.value}.",
            artifact_refs=(report_ref,),
            error_code=None if status is CapabilityStatus.OK else "validation_failed",
            retryable=False,
        )
        immutable_ref = await self._persist_result(task_id, result)
        await self._runtime.complete_operation(task, started.id, immutable_ref)
        return result

    async def publish(self, task_id: str, operation: PublishArtifactOperation) -> CapabilityResult:
        if await self.is_cancelled(task_id):
            return CapabilityResult(status=CapabilityStatus.CANCELLED, summary="Task cancellation was requested.")
        task = await self._runtime.resolve_task(task_id)
        if task is None:
            return CapabilityResult(status=CapabilityStatus.BLOCKED, summary="Task is unavailable for publication.")
        canonical_input = {
            "artifact": operation.artifact.model_dump(mode="json", by_alias=True),
            "validationReport": operation.validation_report.model_dump(mode="json", by_alias=True),
        }
        started = await self._runtime.begin_operation(task, "sandbox.publish", canonical_input)
        if started.status is OperationStatus.COMPLETED and started.immutable_result_ref is not None:
            return await self._load_result(task_id, started.immutable_result_ref)
        try:
            published_ref = await self._store.publish(task_id, operation.artifact, operation.validation_report)
            result = CapabilityResult(
                status=CapabilityStatus.OK,
                summary="Validated artifact version was published.",
                artifact_refs=(published_ref,),
            )
        except ValueError:
            result = CapabilityResult(
                status=CapabilityStatus.BLOCKED,
                summary="Publication requires a passing validation report bound to the candidate artifact.",
                error_code="publication_rejected",
            )
        immutable_ref = await self._persist_result(task_id, result)
        await self._runtime.complete_operation(task, started.id, immutable_ref)
        return result

    async def stop_task(self, task_id: str) -> None:
        task = await self._runtime.resolve_task(task_id)
        identifier = task.active_sandbox_id if task is not None else None
        if identifier is None:
            identifier = self._task_sessions.get(task_id)
        if identifier is None:
            return
        started = time.monotonic()
        try:
            await self.client.stop(identifier)
        except Exception:
            record_session_stop((time.monotonic() - started) * 1000, outcome="failed")
            LOGGER.warning("sandbox deletion failed for task %s; retaining its identity for retry", task_id)
            raise
        record_session_stop((time.monotonic() - started) * 1000, outcome="ok")
        if task is not None:
            await self._runtime.clear_active_sandbox(task_id, identifier)
        self._task_sessions.pop(task_id, None)

    async def _session_for_task(self, task_id: str) -> str:
        existing = self._task_sessions.get(task_id)
        if existing is not None:
            return existing
        proposal = self.client.create_identifier()
        task = await self._runtime.resolve_task(task_id)
        if task is None:
            raise ValueError("task is unavailable for sandbox allocation")
        if task.active_sandbox_id is not None:
            winner = task.active_sandbox_id
        else:
            allocated = await self.client.allocate(task_id, proposal)
            winner = await self._runtime.ensure_active_sandbox(task_id, allocated)
            if winner != allocated:
                await self.client.stop(allocated)
        self._task_sessions[task_id] = winner
        return winner

    @staticmethod
    def _source_name(runtime: Any) -> str:
        if str(runtime) == "python":
            return "source.py"
        return "source.js"

    @staticmethod
    def _map_profile(profile: ValidationProfile) -> SandboxValidationProfile | None:
        mapping: dict[ValidationProfile, SandboxValidationProfile] = {
            ValidationProfile.CORE_XLSX: SandboxValidationProfile.CORE_XLSX,
            ValidationProfile.CORE_HTML: SandboxValidationProfile.CORE_HTML,
            ValidationProfile.WEB_ARTIFACT_HTML: SandboxValidationProfile.WEB_ARTIFACT_HTML,
            ValidationProfile.CORE_CHART: SandboxValidationProfile.CORE_CHART,
            ValidationProfile.CORE_MERMAID: SandboxValidationProfile.CORE_MERMAID,
            ValidationProfile.PROVENANCE: SandboxValidationProfile.PROVENANCE,
            ValidationProfile.DOCUMENT_PPTX: SandboxValidationProfile.DOCUMENT_PPTX,
            ValidationProfile.DOCUMENT_DOCX: SandboxValidationProfile.DOCUMENT_DOCX,
            ValidationProfile.DOCUMENT_XLSX: SandboxValidationProfile.DOCUMENT_XLSX,
            ValidationProfile.DOCUMENT_PDF: SandboxValidationProfile.DOCUMENT_PDF,
        }
        return mapping.get(profile)

    @staticmethod
    def _encode_result_ref(ref: ArtifactRef) -> str:
        return f"{ref.artifact_id}@{ref.version}@{ref.sha256}"

    def _decode_result_ref(self, value: str) -> ArtifactRef:
        parts = value.split("@")
        if len(parts) != 3 or not parts[1].isdigit():
            raise ValueError("operation immutable result ref is malformed")
        artifact_id, version, digest = parts
        return ArtifactRef(artifact_id=artifact_id, version=int(version), kind=ArtifactKind.MANIFEST, sha256=digest)

    async def _persist_result(self, task_id: str, result: CapabilityResult) -> str:
        payload = json.dumps(result.model_dump(mode="json", by_alias=True), ensure_ascii=True, sort_keys=True).encode(
            "utf-8"
        )
        manifest = await self._store.persist_bytes(task_id, ArtifactKind.MANIFEST, "operation-result.json", payload)
        return self._encode_result_ref(manifest)

    async def _load_result(self, task_id: str, immutable_ref: str) -> CapabilityResult:
        ref = self._decode_result_ref(immutable_ref)
        payload = await self._store.read_bytes(task_id, ref)
        return CapabilityResult.model_validate_json(payload)

    @staticmethod
    def _execute_canonical_input(operation: ExecuteSandboxOperation) -> dict[str, Any]:
        canonical: dict[str, Any] = {
            "runtime": operation.runtime.value,
            "source": operation.source,
            "inputArtifacts": [item.model_dump(mode="json", by_alias=True) for item in operation.input_artifacts],
            "parameters": dict(operation.parameters),
            "timeoutSeconds": operation.timeout_seconds,
        }
        if operation.expected_outputs:
            canonical["expectedOutputs"] = list(operation.expected_outputs)
        return canonical
