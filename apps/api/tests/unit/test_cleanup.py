from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from eda_api.cleanup import CleanupService, ExpiredSession, InMemoryCleanupBlobs, InMemoryCleanupWorkspace

FIXED_NOW = datetime(2026, 7, 24, tzinfo=UTC)


@pytest.fixture
def expired() -> ExpiredSession:
    return ExpiredSession(
        partition_key=("tenant-a", "owner-a", "ses_1234567890abcdef"),
        expires_at=FIXED_NOW - timedelta(days=1),
    )


@pytest.fixture
def active() -> ExpiredSession:
    return ExpiredSession(
        partition_key=("tenant-b", "owner-b", "ses_abcdef1234567890"),
        expires_at=FIXED_NOW + timedelta(days=1),
    )


@pytest.fixture
def cleanup_service(expired: ExpiredSession, active: ExpiredSession) -> CleanupService:
    return CleanupService(
        workspace=InMemoryCleanupWorkspace([expired, active]),
        blobs=InMemoryCleanupBlobs(),
    )


@pytest.mark.asyncio
async def test_cleanup_deletes_only_expired_owner_partition(
    cleanup_service: CleanupService, expired: ExpiredSession, active: ExpiredSession
) -> None:
    await cleanup_service.run(now=FIXED_NOW)

    assert cleanup_service.workspace.deleted_partitions == [expired.partition_key]
    assert cleanup_service.blobs.deleted_prefixes == [expired.blob_prefix]
    assert await cleanup_service.workspace.get(active.partition_key) is not None


@pytest.mark.asyncio
async def test_cleanup_retry_is_idempotent(cleanup_service: CleanupService) -> None:
    first = await cleanup_service.run(now=FIXED_NOW)
    second = await cleanup_service.run(now=FIXED_NOW)

    assert first.deleted_sessions == 1
    assert second.deleted_sessions == 0
