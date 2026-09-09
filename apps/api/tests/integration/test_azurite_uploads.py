from __future__ import annotations

import os
import secrets
import socket
from collections.abc import AsyncIterator
from uuid import UUID

import pytest
from azure.storage.blob.aio import BlobServiceClient
from eda_api.auth.models import Principal
from eda_api.storage.uploads import AzureBlobStore, InMemoryUploadRepository, UploadRejected, UploadService

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

AZURITE_CONNECTION_STRING = (
    "DefaultEndpointsProtocol=http;"
    "AccountName=devstoreaccount1;"
    "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;"
    "BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;"
)


async def chunks(values: list[bytes]) -> AsyncIterator[bytes]:
    for value in values:
        yield value


@pytest.fixture
def owner() -> Principal:
    return Principal(
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
        audience=UUID("33333333-3333-3333-3333-333333333333"),
    )


async def test_azurite_quarantine_upload_is_server_named_and_oversize_cleans_up(owner: Principal) -> None:
    if not _azurite_is_available():
        pytest.skip("Azurite must be running for integration tests")
    connection_string = os.getenv("AZURITE_CONNECTION_STRING", AZURITE_CONNECTION_STRING)
    client = BlobServiceClient.from_connection_string(connection_string)
    container_name = f"uploadtest{secrets.token_hex(8)}"
    container = client.get_container_client(container_name)
    repository = InMemoryUploadRepository()
    service = UploadService(
        blob_store=AzureBlobStore(container),
        upload_repository=repository,
        upload_limit_bytes=1024,
    )
    session_id = "ses_1234567890abcdef"

    try:
        await container.create_container()
        record = await service.create_quarantine_upload(
            owner, session_id, "browser.csv", chunks([b"name,value\na,1\n"])
        )
        properties = await container.get_blob_client(record.blob_name).get_blob_properties()

        assert record.blob_name.startswith(f"quarantine/{owner.tenant_id}/{owner.owner_object_id}/upl_")
        assert properties.metadata == {"uploadId": record.id, "sessionId": session_id}
        with pytest.raises(UploadRejected, match="50 MiB"):
            await service.create_quarantine_upload(owner, session_id, "large.csv", chunks([b"0" * 1025]))
        names = [blob.name async for blob in container.list_blobs()]
        assert names == [record.blob_name]
    finally:
        try:
            await container.delete_container()
        finally:
            await client.close()


def _azurite_is_available() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 10000), timeout=1):
            return True
    except OSError:
        return False
