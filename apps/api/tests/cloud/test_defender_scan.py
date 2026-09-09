from __future__ import annotations

import asyncio
import os
import secrets

import pytest
from azure.identity.aio import AzureCliCredential
from azure.storage.blob.aio import BlobServiceClient
from eda_api.storage.scans import MALICIOUS, SCAN_RESULT_TAG

pytestmark = [pytest.mark.cloud, pytest.mark.anyio]

EICAR = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


def required_setting(name: str) -> str:
    value = os.getenv(name)
    if value is None:
        pytest.skip(f"{name} is required for Defender acceptance")
    return value


async def test_defender_marks_eicar_malicious() -> None:
    if os.getenv("EDA_RUN_DEFENDER_ACCEPTANCE") != "1":
        pytest.skip("EDA_RUN_DEFENDER_ACCEPTANCE=1 is required for Defender acceptance")
    account_url = required_setting("EDA_BLOB_ACCOUNT_URL")
    container_name = required_setting("EDA_BLOB_QUARANTINE_CONTAINER")
    credential = AzureCliCredential()
    client = BlobServiceClient(account_url=account_url, credential=credential)
    blob = client.get_container_client(container_name).get_blob_client(
        f"defender-acceptance/{secrets.token_urlsafe(12)}"
    )

    try:
        await blob.upload_blob(EICAR, overwrite=False)
        for delay_seconds in (1, 2, 4, 8, 16, 32, 60, 60, 60, 60, 60, 60):
            tags = await blob.get_blob_tags()
            if tags.get(SCAN_RESULT_TAG) == MALICIOUS:
                break
            await asyncio.sleep(delay_seconds)
        else:
            pytest.fail("Defender did not classify EICAR as malicious within the acceptance window")
        assert (await blob.get_blob_tags()).get(SCAN_RESULT_TAG) == MALICIOUS
    finally:
        await blob.delete_blob(delete_snapshots="include")
        await client.close()
        await credential.close()
