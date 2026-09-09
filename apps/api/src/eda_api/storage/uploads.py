from __future__ import annotations

import asyncio
import hashlib
import secrets
import tempfile
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import BinaryIO, Protocol, cast

from azure.core import MatchConditions
from azure.core.exceptions import HttpResponseError, ResourceNotFoundError
from azure.cosmos.aio import ContainerProxy
from azure.cosmos.exceptions import CosmosResourceNotFoundError
from azure.storage.blob.aio import ContainerClient
from eda_api.auth.models import Principal
from eda_runtime_state.models import TaskPartition

from .archive_policy import OFFICE_ROOTS, UnsafeArchive, inspect_office_archive
from .blob_names import BlobNameError, quarantine_blob_name
from .models import UploadRecord
from .scans import CLEAN, SCAN_RESULT_TAG, ScanService, ScanState

MAX_UPLOADS_PER_SESSION = 10
SPOOL_MAX_MEMORY_BYTES = 8 * 1024 * 1024
TRANSFER_CHUNK_BYTES = 1024 * 1024
ALLOWED_EXTENSIONS = {".csv", ".jpg", ".jpeg", ".png", *OFFICE_ROOTS}


class UploadRejected(ValueError):
    pass


class UploadNotReady(ValueError):
    def __init__(self, state: ScanState) -> None:
        super().__init__("upload is not clean")
        self.state = state


class BlobStore(Protocol):
    async def upload(self, name: str, chunks: AsyncIterator[bytes], metadata: dict[str, str]) -> str: ...

    async def delete(self, name: str) -> None: ...

    async def get_tags(self, name: str) -> dict[str, str]: ...

    def download_clean(self, record: UploadRecord) -> AsyncIterator[bytes]: ...


class UploadRepository(Protocol):
    async def count_for_session(self, principal: Principal, session_id: str) -> int: ...

    async def create_upload(self, record: UploadRecord) -> None: ...

    async def get_upload(
        self, principal: Principal | TaskPartition, session_id: str, upload_id: str
    ) -> UploadRecord | None: ...

    async def delete_upload(self, principal: Principal, session_id: str, upload_id: str) -> None: ...


class InMemoryBlobStore:
    def __init__(self) -> None:
        self._blobs: dict[str, tuple[bytes, dict[str, str]]] = {}
        self._etags: dict[str, str] = {}

    async def upload(self, name: str, chunks: AsyncIterator[bytes], metadata: dict[str, str]) -> str:
        if name in self._blobs:
            raise FileExistsError(name)
        parts: list[bytes] = []
        async for chunk in chunks:
            parts.append(chunk)
        self._blobs[name] = b"".join(parts), metadata
        self._etags[name] = secrets.token_hex(16)
        return self._etags[name]

    async def delete(self, name: str) -> None:
        self._blobs.pop(name, None)
        self._etags.pop(name, None)

    async def get_tags(self, name: str) -> dict[str, str]:
        if name not in self._blobs:
            raise FileNotFoundError(name)
        return {}

    async def download_clean(self, record: UploadRecord) -> AsyncIterator[bytes]:
        if not record.blob_etag or self._etags.get(record.blob_name) != record.blob_etag:
            raise UploadRejected("upload identity or digest changed")
        outcome = ScanService().classify(await self.get_tags(record.blob_name))
        if not outcome.promotable:
            raise UploadNotReady(outcome.state)
        content, metadata = self._blobs[record.blob_name]
        _validate_quarantine_properties(record, metadata, len(content))
        yield content

    def names(self) -> list[str]:
        return sorted(self._blobs)


class InMemoryUploadRepository:
    def __init__(self) -> None:
        self._uploads: dict[tuple[str, str, str, str], UploadRecord] = {}

    async def count_for_session(self, principal: Principal, session_id: str) -> int:
        prefix = str(principal.tenant_id), str(principal.owner_object_id), session_id
        return sum(key[:3] == prefix for key in self._uploads)

    async def create_upload(self, record: UploadRecord) -> None:
        self._uploads[self._key(record.tenant_id, record.owner_object_id, record.session_id, record.id)] = record

    async def get_upload(
        self, principal: Principal | TaskPartition, session_id: str, upload_id: str
    ) -> UploadRecord | None:
        return self._uploads.get(self._key(principal.tenant_id, principal.owner_object_id, session_id, upload_id))

    async def delete_upload(self, principal: Principal, session_id: str, upload_id: str) -> None:
        self._uploads.pop(self._key(principal.tenant_id, principal.owner_object_id, session_id, upload_id), None)

    @staticmethod
    def _key(tenant_id: object, owner_object_id: object, session_id: str, upload_id: str) -> tuple[str, str, str, str]:
        return str(tenant_id), str(owner_object_id), session_id, upload_id


class AzureBlobStore:
    def __init__(self, container: ContainerClient) -> None:
        self._container = container

    async def upload(self, name: str, chunks: AsyncIterator[bytes], metadata: dict[str, str]) -> str:
        result = await self._container.get_blob_client(name).upload_blob(chunks, overwrite=False, metadata=metadata)
        return str(result["etag"])

    async def delete(self, name: str) -> None:
        await self._container.delete_blob(name)

    async def get_tags(self, name: str) -> dict[str, str]:
        return await self._container.get_blob_client(name).get_blob_tags()

    async def download_clean(self, record: UploadRecord) -> AsyncIterator[bytes]:
        if not record.blob_etag:
            raise UploadRejected("upload identity or digest is unavailable")
        blob = self._container.get_blob_client(record.blob_name)
        try:
            properties = await blob.get_blob_properties(
                etag=record.blob_etag, match_condition=MatchConditions.IfNotModified
            )
            _validate_quarantine_properties(record, properties.metadata, properties.size)
            stream = await blob.download_blob(
                etag=record.blob_etag,
                match_condition=MatchConditions.IfNotModified,
                if_tags_match_condition=f'"{SCAN_RESULT_TAG}" = \'{CLEAN}\'',
                max_concurrency=1,
                decompress=False,
            )
            async for chunk in stream.chunks():
                yield chunk
        except HttpResponseError as error:
            if error.status_code in {404, 412}:
                raise UploadRejected("upload identity or digest changed") from error
            raise


class CosmosUploadRepository:
    def __init__(self, container: ContainerProxy) -> None:
        self._container = container

    async def count_for_session(self, principal: Principal, session_id: str) -> int:
        query = (
            "SELECT VALUE COUNT(1) FROM c WHERE c.tenantId = @tenantId "
            "AND c.ownerObjectId = @ownerObjectId AND c.sessionId = @sessionId AND c.recordType = 'upload'"
        )
        parameters: list[dict[str, object]] = [
            {"name": "@tenantId", "value": str(principal.tenant_id)},
            {"name": "@ownerObjectId", "value": str(principal.owner_object_id)},
            {"name": "@sessionId", "value": session_id},
        ]
        items = self._container.query_items(
            query=query,
            parameters=parameters,
            partition_key=[str(principal.tenant_id), str(principal.owner_object_id), session_id],
        )
        async for count in items:
            if isinstance(count, int):
                return count
        return 0

    async def create_upload(self, record: UploadRecord) -> None:
        await self._container.create_item(record.model_dump(mode="json", by_alias=True))

    async def get_upload(
        self, principal: Principal | TaskPartition, session_id: str, upload_id: str
    ) -> UploadRecord | None:
        try:
            item = await self._container.read_item(
                item=upload_id,
                partition_key=[str(principal.tenant_id), str(principal.owner_object_id), session_id],
            )
        except CosmosResourceNotFoundError:
            return None
        if item.get("recordType") != "upload":
            return None
        return UploadRecord.model_validate(item)

    async def delete_upload(self, principal: Principal, session_id: str, upload_id: str) -> None:
        try:
            await self._container.delete_item(
                item=upload_id,
                partition_key=[str(principal.tenant_id), str(principal.owner_object_id), session_id],
            )
        except Exception as error:
            from azure.cosmos.exceptions import CosmosResourceNotFoundError

            if isinstance(error, CosmosResourceNotFoundError):
                return
            raise


class UploadService:
    def __init__(
        self,
        *,
        blob_store: BlobStore,
        upload_repository: UploadRepository,
        upload_limit_bytes: int,
    ) -> None:
        self.blob_store = blob_store
        self.upload_repository = upload_repository
        self._upload_limit_bytes = upload_limit_bytes

    async def get_status(
        self, principal: Principal | TaskPartition, session_id: str, upload_id: str
    ) -> UploadRecord | None:
        record = await self.upload_repository.get_upload(principal, session_id, upload_id)
        if record is None or (
            record.tenant_id != principal.tenant_id
            or record.owner_object_id != principal.owner_object_id
            or record.session_id != session_id
            or record.id != upload_id
            or record.blob_name != quarantine_blob_name(principal, record.id)
        ):
            return None
        try:
            tags = await self.blob_store.get_tags(record.blob_name)
        except (ResourceNotFoundError, FileNotFoundError):
            state: ScanState = "scan_failed"
        else:
            state = ScanService().classify(tags).state
        return UploadRecord.model_validate({**record.model_dump(), "state": state})

    async def read_clean_upload(
        self, principal: Principal | TaskPartition, session_id: str, upload_id: str
    ) -> tuple[UploadRecord, bytes]:
        record = await self.get_status(principal, session_id, upload_id)
        if record is None:
            raise LookupError("upload is unavailable in session scope")
        if record.state != "clean":
            raise UploadNotReady(record.state)
        if not record.blob_etag:
            raise UploadRejected("upload identity or digest is unavailable")
        content = bytearray()
        digest = hashlib.sha256()
        async for chunk in self.blob_store.download_clean(record):
            if len(content) + len(chunk) > min(record.size_bytes, self._upload_limit_bytes):
                raise UploadRejected("upload identity or digest changed")
            content.extend(chunk)
            digest.update(chunk)
        if len(content) != record.size_bytes or digest.hexdigest() != record.sha256:
            raise UploadRejected("upload identity or digest changed")
        return record, bytes(content)

    async def create_quarantine_upload(
        self,
        principal: Principal,
        session_id: str,
        filename: str,
        chunks: AsyncIterator[bytes],
    ) -> UploadRecord:
        if await self.upload_repository.count_for_session(principal, session_id) >= MAX_UPLOADS_PER_SESSION:
            raise UploadRejected("session upload limit reached")
        display_name, extension = _display_name_and_extension(filename)
        if extension not in ALLOWED_EXTENSIONS:
            raise UploadRejected("unsupported upload extension")

        upload_id = f"upl_{secrets.token_urlsafe(24)}"
        try:
            blob_name = quarantine_blob_name(principal, upload_id)
        except BlobNameError as error:
            raise UploadRejected("could not allocate upload") from error
        spool = cast(
            BinaryIO,
            await asyncio.to_thread(tempfile.SpooledTemporaryFile, max_size=SPOOL_MAX_MEMORY_BYTES, mode="w+b"),
        )
        uploaded = False
        record_created = False
        try:
            size_bytes, digest = await _write_and_hash(spool, chunks, self._upload_limit_bytes)
            if extension in OFFICE_ROOTS:
                await asyncio.to_thread(inspect_office_archive, spool, extension)
            await asyncio.to_thread(spool.seek, 0)
            blob_etag = await self.blob_store.upload(
                blob_name,
                _spool_chunks(spool),
                {"uploadId": upload_id, "sessionId": session_id},
            )
            uploaded = True
            record = UploadRecord(
                id=upload_id,
                tenant_id=principal.tenant_id,
                owner_object_id=principal.owner_object_id,
                session_id=session_id,
                blob_name=blob_name,
                blob_etag=blob_etag,
                display_name=display_name,
                sha256=digest,
                size_bytes=size_bytes,
                created_at=datetime.now(UTC),
            )
            await self.upload_repository.create_upload(record)
            record_created = True
            return record
        except UnsafeArchive as error:
            raise UploadRejected("unsafe Office archive") from error
        except UploadRejected:
            raise
        finally:
            if record_created is False:
                await self.upload_repository.delete_upload(principal, session_id, upload_id)
            if uploaded and record_created is False:
                await self.blob_store.delete(blob_name)
            await asyncio.to_thread(spool.close)


def _validate_quarantine_properties(record: UploadRecord, metadata: dict[str, str], size_bytes: int) -> None:
    normalized = {key.lower(): value for key, value in metadata.items()}
    if (
        normalized.get("uploadid") != record.id
        or normalized.get("sessionid") != record.session_id
        or size_bytes != record.size_bytes
    ):
        raise UploadRejected("upload identity or digest changed")


async def _write_and_hash(spool: BinaryIO, chunks: AsyncIterator[bytes], limit: int) -> tuple[int, str]:
    digest = hashlib.sha256()
    size_bytes = 0
    async for chunk in chunks:
        size_bytes += len(chunk)
        if size_bytes > limit:
            raise UploadRejected("upload exceeds 50 MiB")
        digest.update(chunk)
        await asyncio.to_thread(spool.write, chunk)
    return size_bytes, digest.hexdigest()


async def _spool_chunks(spool: BinaryIO) -> AsyncIterator[bytes]:
    while chunk := await asyncio.to_thread(spool.read, TRANSFER_CHUNK_BYTES):
        yield chunk


def _display_name_and_extension(filename: str) -> tuple[str, str]:
    display_name = PurePosixPath(filename.replace("\\", "/")).name
    extension = PurePosixPath(display_name).suffix.lower()
    if not display_name or display_name in {".", ".."}:
        raise UploadRejected("invalid upload filename")
    return display_name, extension
