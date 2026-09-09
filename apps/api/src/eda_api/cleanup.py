from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


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
        for session in sessions:
            await self.workspace.delete_partition(session.partition_key)
            await self.blobs.delete_prefix(session.blob_prefix)
        return CleanupResult(deleted_sessions=len(sessions), dry_run=False)


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
