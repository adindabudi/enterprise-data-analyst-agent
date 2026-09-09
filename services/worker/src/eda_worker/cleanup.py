from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, cast

from azure.cosmos.aio import ContainerProxy
from azure.storage.blob.aio import ContainerClient


@dataclass(frozen=True)
class ExpiredSession:
    partition_key: tuple[str, str, str]
    expires_at: datetime

    @property
    def blob_prefix(self) -> str:
        tenant_id, owner_object_id, session_id = self.partition_key
        return f"sessions/{tenant_id}/{owner_object_id}/{session_id}/"


@dataclass(frozen=True)
class CleanupResult:
    deleted_sessions: int
    dry_run: bool
    failures: tuple[CleanupFailure, ...] = ()

    @property
    def failed_sessions(self) -> int:
        return len(self.failures)


@dataclass(frozen=True)
class CleanupFailure:
    partition_key: tuple[str, str, str]


class CleanupWorkspace(Protocol):
    async def expired_before(self, before: datetime, limit: int) -> list[ExpiredSession]: ...

    async def delete_partition(self, partition_key: tuple[str, str, str]) -> None: ...


class CleanupBlobs(Protocol):
    async def delete_prefix(self, prefix: str) -> None: ...


class CleanupService:
    def __init__(self, *, workspace: CleanupWorkspace, blobs: CleanupBlobs) -> None:
        self.workspace = workspace
        self.blobs = blobs

    async def run(self, *, now: datetime, limit: int = 100, dry_run: bool = False) -> CleanupResult:
        if limit < 1 or limit > 100:
            raise ValueError("cleanup limit must be between 1 and 100")
        sessions = await self.workspace.expired_before(now, limit)
        if dry_run:
            return CleanupResult(deleted_sessions=len(sessions), dry_run=True)
        deleted_sessions = 0
        failures: list[CleanupFailure] = []
        for session in sessions:
            try:
                # Delete blobs first so a Cosmos failure leaves an idempotently retryable record.
                await self.blobs.delete_prefix(session.blob_prefix)
                await self.workspace.delete_partition(session.partition_key)
            except Exception:
                failures.append(CleanupFailure(partition_key=session.partition_key))
            else:
                deleted_sessions += 1
        return CleanupResult(deleted_sessions=deleted_sessions, dry_run=False, failures=tuple(failures))


class CosmosCleanupWorkspace:
    def __init__(self, container: ContainerProxy) -> None:
        self._container = container

    async def expired_before(self, before: datetime, limit: int) -> list[ExpiredSession]:
        query = (
            "SELECT c.tenantId, c.ownerObjectId, c.sessionId, c.expiresAt FROM c "
            "WHERE IS_DEFINED(c.expiresAt) AND c.expiresAt < @before "
            "ORDER BY c.expiresAt OFFSET 0 LIMIT @limit"
        )
        parameters: list[dict[str, object]] = [
            {"name": "@before", "value": before.isoformat()},
            {"name": "@limit", "value": limit},
        ]
        iterator = self._container.query_items(query=query, parameters=parameters)
        sessions: list[ExpiredSession] = []
        async for item in iterator:
            session = self._session_from_item(item)
            if session is not None:
                sessions.append(session)
        return sessions

    async def delete_partition(self, partition_key: tuple[str, str, str]) -> None:
        tenant_id, owner_object_id, session_id = partition_key
        partition = [tenant_id, owner_object_id, session_id]
        iterator = self._container.query_items(query="SELECT c.id FROM c", partition_key=partition)
        item_ids: list[str] = []
        async for item in iterator:
            item_id = item.get("id", session_id)
            if isinstance(item_id, str):
                item_ids.append(item_id)
        for start in range(0, len(item_ids), 100):
            operations = [("delete", (item_id,)) for item_id in item_ids[start : start + 100]]
            await self._container.execute_item_batch(batch_operations=operations, partition_key=partition)

    @staticmethod
    def _session_from_item(item: dict[str, Any]) -> ExpiredSession | None:
        tenant_id = item.get("tenantId")
        owner_object_id = item.get("ownerObjectId")
        session_id = item.get("sessionId")
        expires_at = item.get("expiresAt")
        if not all(isinstance(value, str) for value in (tenant_id, owner_object_id, session_id, expires_at)):
            return None
        tenant_id_text = cast(str, tenant_id)
        owner_object_id_text = cast(str, owner_object_id)
        session_id_text = cast(str, session_id)
        expires_at_text = cast(str, expires_at)
        parsed_expiration = datetime.fromisoformat(expires_at_text.replace("Z", "+00:00"))
        if parsed_expiration.tzinfo is None:
            return None
        return ExpiredSession(
            partition_key=(tenant_id_text, owner_object_id_text, session_id_text),
            expires_at=parsed_expiration.astimezone(UTC),
        )


class AzureBlobCleanup:
    def __init__(self, container: ContainerClient) -> None:
        self._container = container

    async def delete_prefix(self, prefix: str) -> None:
        async for blob in self._container.list_blobs(name_starts_with=prefix):
            await self._container.delete_blob(blob.name)


def parse_cleanup_before(value: str, *, now: datetime | None = None) -> datetime:
    if value == "now":
        return now or datetime.now(UTC)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("cleanup --before must be an offset-aware ISO timestamp or 'now'")
    return parsed.astimezone(UTC)


class InMemoryCleanupWorkspace:
    def __init__(self, sessions: list[ExpiredSession]) -> None:
        self._sessions = {session.partition_key: session for session in sessions}
        self.deleted_partitions: list[tuple[str, str, str]] = []

    async def expired_before(self, before: datetime, limit: int) -> list[ExpiredSession]:
        candidates = [session for session in self._sessions.values() if session.expires_at < before]
        return sorted(candidates, key=lambda session: session.expires_at)[:limit]

    async def delete_partition(self, partition_key: tuple[str, str, str]) -> None:
        if self._sessions.pop(partition_key, None) is not None:
            self.deleted_partitions.append(partition_key)

    async def get(self, partition_key: tuple[str, str, str]) -> ExpiredSession | None:
        return self._sessions.get(partition_key)


class InMemoryCleanupBlobs:
    def __init__(self) -> None:
        self.deleted_prefixes: list[str] = []

    async def delete_prefix(self, prefix: str) -> None:
        if prefix not in self.deleted_prefixes:
            self.deleted_prefixes.append(prefix)
