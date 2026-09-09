from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from eda_worker.cleanup import (
    CleanupService,
    CosmosCleanupWorkspace,
    ExpiredSession,
    InMemoryCleanupBlobs,
    InMemoryCleanupWorkspace,
    parse_cleanup_before,
)

FIXED_NOW = datetime(2026, 7, 25, tzinfo=UTC)


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


@pytest.mark.asyncio
async def test_cleanup_deletes_only_expired_owner_partition(expired: ExpiredSession, active: ExpiredSession) -> None:
    workspace = InMemoryCleanupWorkspace([expired, active])
    blobs = InMemoryCleanupBlobs()

    result = await CleanupService(workspace=workspace, blobs=blobs).run(now=FIXED_NOW)

    assert result.deleted_sessions == 1
    assert workspace.deleted_partitions == [expired.partition_key]
    assert blobs.deleted_prefixes == [expired.blob_prefix]
    assert await workspace.get(active.partition_key) is not None


@pytest.mark.asyncio
async def test_cleanup_retry_is_idempotent(expired: ExpiredSession) -> None:
    service = CleanupService(workspace=InMemoryCleanupWorkspace([expired]), blobs=InMemoryCleanupBlobs())

    first = await service.run(now=FIXED_NOW)
    second = await service.run(now=FIXED_NOW)

    assert first.deleted_sessions == 1
    assert second.deleted_sessions == 0


@pytest.mark.asyncio
async def test_cosmos_cleanup_workspace_uses_expiration_query_and_partition_batch_deletes() -> None:
    class FakeContainer:
        def __init__(self) -> None:
            self.queries: list[dict[str, object]] = []
            self.batches: list[dict[str, object]] = []

        def query_items(self, **kwargs: object):  # type: ignore[no-untyped-def]
            self.queries.append(kwargs)

            async def items():
                yield {
                    "tenantId": "tenant-a",
                    "ownerObjectId": "owner-a",
                    "sessionId": "ses_1234567890abcdef",
                    "expiresAt": "2026-07-24T00:00:00+00:00",
                }

            return items()

        async def execute_item_batch(self, **kwargs: object) -> None:
            self.batches.append(kwargs)

    container = FakeContainer()
    workspace = CosmosCleanupWorkspace(container)  # type: ignore[arg-type]

    sessions = await workspace.expired_before(FIXED_NOW, limit=3)
    await workspace.delete_partition(sessions[0].partition_key)

    assert sessions == [
        ExpiredSession(
            partition_key=("tenant-a", "owner-a", "ses_1234567890abcdef"),
            expires_at=datetime(2026, 7, 24, tzinfo=UTC),
        )
    ]
    assert "enable_cross_partition_query" not in container.queries[0]
    assert container.queries[0]["parameters"] == [
        {"name": "@before", "value": FIXED_NOW.isoformat()},
        {"name": "@limit", "value": 3},
    ]
    assert container.batches == [
        {
            "batch_operations": [("delete", ("ses_1234567890abcdef",))],
            "partition_key": ["tenant-a", "owner-a", "ses_1234567890abcdef"],
        }
    ]


@pytest.mark.asyncio
async def test_cleanup_reports_per_session_failures_without_skipping_remaining_expired_sessions(
    expired: ExpiredSession,
) -> None:
    second_expired = ExpiredSession(
        partition_key=("tenant-c", "owner-c", "ses_aaaaaaaaaaaaaaaa"),
        expires_at=FIXED_NOW - timedelta(hours=2),
    )

    class FailingBlobs(InMemoryCleanupBlobs):
        async def delete_prefix(self, prefix: str) -> None:
            if prefix == expired.blob_prefix:
                raise RuntimeError("blob unavailable")
            await super().delete_prefix(prefix)

    workspace = InMemoryCleanupWorkspace([expired, second_expired])
    result = await CleanupService(workspace=workspace, blobs=FailingBlobs()).run(now=FIXED_NOW)

    assert result.deleted_sessions == 1
    assert result.failed_sessions == 1
    assert await workspace.get(second_expired.partition_key) is None
    assert await workspace.get(expired.partition_key) is not None


def test_cleanup_before_accepts_now_or_an_offset_aware_iso_timestamp() -> None:
    assert parse_cleanup_before("now", now=FIXED_NOW) == FIXED_NOW
    assert parse_cleanup_before("2026-07-24T00:00:00Z") == datetime(2026, 7, 24, tzinfo=UTC)
    with pytest.raises(ValueError, match="offset-aware"):
        parse_cleanup_before("2026-07-24T00:00:00")
