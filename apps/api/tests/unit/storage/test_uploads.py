from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID

import pytest
from azure.core.exceptions import HttpResponseError, ResourceNotFoundError
from eda_api.auth.models import Principal
from eda_api.storage.models import UploadRecord
from eda_api.storage.scans import SCAN_RESULT_TAG
from eda_api.storage.uploads import (
    InMemoryBlobStore,
    InMemoryUploadRepository,
    UploadNotReady,
    UploadRejected,
    UploadService,
)

OWNER = Principal(
    tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
    owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
    audience=UUID("33333333-3333-3333-3333-333333333333"),
)
SESSION_ID = "ses_1234567890abcdef"


async def chunks(values: list[bytes]) -> AsyncIterator[bytes]:
    for value in values:
        yield value


@pytest.fixture
def blob_store() -> InMemoryBlobStore:
    return InMemoryBlobStore()


@pytest.fixture
def upload_repository() -> InMemoryUploadRepository:
    return InMemoryUploadRepository()


@pytest.fixture
def upload_service(blob_store: InMemoryBlobStore, upload_repository: InMemoryUploadRepository) -> UploadService:
    return UploadService(
        blob_store=blob_store,
        upload_repository=upload_repository,
        upload_limit_bytes=50 * 1024 * 1024,
    )


@pytest.mark.asyncio
async def test_upload_over_50_mib_is_deleted(
    upload_service: UploadService, blob_store: InMemoryBlobStore, upload_repository: InMemoryUploadRepository
) -> None:
    with pytest.raises(UploadRejected, match="50 MiB"):
        await upload_service.create_quarantine_upload(
            OWNER,
            SESSION_ID,
            "large.csv",
            chunks([b"0" * (50 * 1024 * 1024 + 1)]),
        )

    assert blob_store.names() == []
    assert await upload_repository.count_for_session(OWNER, SESSION_ID) == 0


@pytest.mark.asyncio
async def test_valid_csv_upload_is_scanning_and_server_named(
    upload_service: UploadService, blob_store: InMemoryBlobStore
) -> None:
    record = await upload_service.create_quarantine_upload(
        OWNER,
        SESSION_ID,
        "../../browser-name.csv",
        chunks([b"category,value\nrevenue,42\n"]),
    )

    assert record.state == "scanning"
    assert record.blob_name.startswith(f"quarantine/{OWNER.tenant_id}/{OWNER.owner_object_id}/upl_")
    assert blob_store.names() == [record.blob_name]


class ScannedBlobStore(InMemoryBlobStore):
    def __init__(self) -> None:
        super().__init__()
        self.scan_result: str | None = None
        self.downloads = 0
        self.download_content: bytes | None = None

    async def get_tags(self, name: str) -> dict[str, str]:
        del name
        return {} if self.scan_result is None else {SCAN_RESULT_TAG: self.scan_result}

    async def download_clean(self, record: UploadRecord) -> AsyncIterator[bytes]:
        self.downloads += 1
        if self.download_content is not None:
            yield self.download_content
            return
        async for chunk in super().download_clean(record):
            yield chunk


@pytest.fixture
def scanned_uploads() -> tuple[UploadService, ScannedBlobStore]:
    blobs = ScannedBlobStore()
    service = UploadService(
        blob_store=blobs, upload_repository=InMemoryUploadRepository(), upload_limit_bytes=50 * 1024 * 1024
    )
    return service, blobs


@pytest.mark.parametrize("scan_result", [None, "Malicious", "Error", "Not scanned", "unknown"])
@pytest.mark.asyncio
async def test_unclean_upload_is_never_downloaded(
    scanned_uploads: tuple[UploadService, ScannedBlobStore], scan_result: str | None
) -> None:
    service, blobs = scanned_uploads
    record = await service.create_quarantine_upload(OWNER, SESSION_ID, "data.csv", chunks([b"value\n42\n"]))
    await service.upload_repository.create_upload(record.model_copy(update={"state": "clean"}))
    blobs.scan_result = scan_result

    with pytest.raises(ValueError, match="upload is not clean"):
        await service.read_clean_upload(OWNER, SESSION_ID, record.id)

    assert blobs.downloads == 0


@pytest.mark.parametrize("scope", ["owner", "tenant", "session", "missing"])
@pytest.mark.asyncio
async def test_clean_upload_read_is_owner_and_session_scoped(
    scanned_uploads: tuple[UploadService, ScannedBlobStore], scope: str
) -> None:
    service, blobs = scanned_uploads
    record = await service.create_quarantine_upload(OWNER, SESSION_ID, "data.csv", chunks([b"value\n42\n"]))
    blobs.scan_result = "No threats found"
    principal = OWNER
    session_id, upload_id = SESSION_ID, record.id
    if scope == "owner":
        principal = OWNER.model_copy(update={"owner_object_id": UUID(int=4)})
    elif scope == "tenant":
        principal = OWNER.model_copy(update={"tenant_id": UUID(int=5)})
    elif scope == "session":
        session_id = "ses_other1234567890"
    else:
        upload_id = "upl_missing1234567890"

    with pytest.raises(LookupError):
        await service.read_clean_upload(principal, session_id, upload_id)

    assert blobs.downloads == 0


@pytest.mark.asyncio
async def test_clean_upload_returns_the_server_verified_bytes(scanned_uploads: tuple[UploadService, ScannedBlobStore]) -> None:
    service, blobs = scanned_uploads
    content = b"value\n42\n"
    record = await service.create_quarantine_upload(OWNER, SESSION_ID, "data.csv", chunks([content]))
    blobs.scan_result = "No threats found"

    verified, actual = await service.read_clean_upload(OWNER, SESSION_ID, record.id)

    assert actual == content
    assert verified.sha256 == record.sha256
    assert verified.state == "clean"
    assert verified.blob_etag
    assert blobs.downloads == 1


@pytest.mark.parametrize("content", [b"value\n43\n", b"short"])
@pytest.mark.asyncio
async def test_clean_upload_rechecks_digest_and_size(
    scanned_uploads: tuple[UploadService, ScannedBlobStore], content: bytes
) -> None:
    service, blobs = scanned_uploads
    record = await service.create_quarantine_upload(OWNER, SESSION_ID, "data.csv", chunks([b"value\n42\n"]))
    blobs.scan_result = "No threats found"
    blobs.download_content = content

    with pytest.raises(UploadRejected, match="identity or digest"):
        await service.read_clean_upload(OWNER, SESSION_ID, record.id)


@pytest.mark.parametrize("error_type", [ResourceNotFoundError, FileNotFoundError])
@pytest.mark.parametrize("cached_state", ["scanning", "clean"])
@pytest.mark.asyncio
async def test_missing_quarantine_blob_is_scan_failed_without_malicious_classification(
    scanned_uploads: tuple[UploadService, ScannedBlobStore],
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[Exception],
    cached_state: str,
) -> None:
    service, blobs = scanned_uploads
    record = await service.create_quarantine_upload(OWNER, SESSION_ID, "data.csv", chunks([b"value\n42\n"]))
    await service.upload_repository.create_upload(record.model_copy(update={"state": cached_state}))

    async def missing_tags(name: str) -> dict[str, str]:
        assert name == record.blob_name
        raise error_type("quarantine blob is no longer available")

    monkeypatch.setattr(blobs, "get_tags", missing_tags)
    status = await service.get_status(OWNER, SESSION_ID, record.id)

    assert status is not None and status.state == "scan_failed"
    with pytest.raises(UploadNotReady) as error:
        await service.read_clean_upload(OWNER, SESSION_ID, record.id)
    assert error.value.state == "scan_failed"
    assert blobs.downloads == 0


@pytest.mark.parametrize("status_code", [403, 503])
@pytest.mark.asyncio
async def test_scan_status_does_not_misclassify_access_or_service_errors(
    scanned_uploads: tuple[UploadService, ScannedBlobStore], monkeypatch: pytest.MonkeyPatch, status_code: int
) -> None:
    service, blobs = scanned_uploads
    record = await service.create_quarantine_upload(OWNER, SESSION_ID, "data.csv", chunks([b"value\n42\n"]))
    failure = HttpResponseError(message="tag lookup unavailable")
    failure.status_code = status_code

    async def unavailable_tags(name: str) -> dict[str, str]:
        del name
        raise failure

    monkeypatch.setattr(blobs, "get_tags", unavailable_tags)
    with pytest.raises(HttpResponseError) as error:
        await service.get_status(OWNER, SESSION_ID, record.id)
    assert error.value is failure
    assert error.value.status_code == status_code


@pytest.mark.asyncio
async def test_replaced_blob_cannot_reuse_a_clean_scan(scanned_uploads: tuple[UploadService, ScannedBlobStore]) -> None:
    service, blobs = scanned_uploads
    content = b"value\n42\n"
    record = await service.create_quarantine_upload(OWNER, SESSION_ID, "data.csv", chunks([content]))
    await blobs.delete(record.blob_name)
    await blobs.upload(
        record.blob_name, chunks([content]), {"uploadId": record.id, "sessionId": record.session_id}
    )
    blobs.scan_result = "No threats found"

    with pytest.raises(UploadRejected, match="identity or digest"):
        await service.read_clean_upload(OWNER, SESSION_ID, record.id)
