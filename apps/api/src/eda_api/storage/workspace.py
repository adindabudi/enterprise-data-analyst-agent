from __future__ import annotations

import secrets
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Protocol, cast

from azure.core import MatchConditions
from azure.cosmos.aio import ContainerProxy
from azure.cosmos.exceptions import CosmosHttpResponseError, CosmosResourceNotFoundError
from eda_api.auth.models import Principal

from .models import WorkspaceSession


class StorageConflict(ValueError):
    pass


class WorkspaceRepository(Protocol):
    async def create_session(self, principal: Principal, title: str) -> WorkspaceSession: ...

    async def get_session(self, principal: Principal, session_id: str) -> WorkspaceSession | None: ...

    async def list_sessions(self, principal: Principal) -> list[WorkspaceSession]: ...

    async def rename_session(
        self, principal: Principal, session_id: str, title: str, expected_etag: str
    ) -> WorkspaceSession: ...

    async def delete_session_records(self, principal: Principal, session_id: str) -> None: ...


class InMemoryWorkspaceRepository:
    def __init__(self) -> None:
        self._sessions: dict[tuple[str, str, str], WorkspaceSession] = {}
        self._etag_counter = 0

    async def create_session(self, principal: Principal, title: str) -> WorkspaceSession:
        now = datetime.now(UTC)
        session_id = f"ses_{secrets.token_urlsafe(24)}"
        session = WorkspaceSession(
            id=session_id,
            tenant_id=principal.tenant_id,
            owner_object_id=principal.owner_object_id,
            session_id=session_id,
            title=title,
            created_at=now,
            last_activity_at=now,
            expires_at=now + timedelta(days=30),
            _etag=self._next_etag(),
        )
        self._sessions[self._key(principal, session_id)] = session
        return session

    async def get_session(self, principal: Principal, session_id: str) -> WorkspaceSession | None:
        return self._sessions.get(self._key(principal, session_id))

    async def list_sessions(self, principal: Principal) -> list[WorkspaceSession]:
        prefix = (str(principal.tenant_id), str(principal.owner_object_id))
        return sorted(
            (session for key, session in self._sessions.items() if key[:2] == prefix),
            key=lambda session: session.last_activity_at, reverse=True,
        )

    async def rename_session(
        self, principal: Principal, session_id: str, title: str, expected_etag: str
    ) -> WorkspaceSession:
        key = self._key(principal, session_id)
        session = self._sessions.get(key)
        if session is None or session.etag != expected_etag:
            raise StorageConflict("workspace session has changed")
        updated = session.model_copy(
            update={"title": title, "last_activity_at": datetime.now(UTC), "etag": self._next_etag()}
        )
        self._sessions[key] = updated
        return updated

    async def delete_session_records(self, principal: Principal, session_id: str) -> None:
        self._sessions.pop(self._key(principal, session_id), None)

    def _key(self, principal: Principal, session_id: str) -> tuple[str, str, str]:
        return str(principal.tenant_id), str(principal.owner_object_id), session_id

    def _next_etag(self) -> str:
        self._etag_counter += 1
        return f'"{self._etag_counter}"'


class CosmosWorkspaceRepository:
    def __init__(self, container: ContainerProxy) -> None:
        self._container = container

    @staticmethod
    def partition(principal: Principal, session_id: str) -> list[str]:
        return [str(principal.tenant_id), str(principal.owner_object_id), session_id]

    async def create_session(self, principal: Principal, title: str) -> WorkspaceSession:
        now = datetime.now(UTC)
        session_id = f"ses_{secrets.token_urlsafe(24)}"
        session = WorkspaceSession(
            id=session_id,
            tenant_id=principal.tenant_id,
            owner_object_id=principal.owner_object_id,
            session_id=session_id,
            title=title,
            created_at=now,
            last_activity_at=now,
            expires_at=now + timedelta(days=30),
        )
        document = await self._container.create_item(session.model_dump(mode="json", by_alias=True, exclude={"etag"}))
        return WorkspaceSession.model_validate(document)

    async def get_session(self, principal: Principal, session_id: str) -> WorkspaceSession | None:
        try:
            body = await self._container.read_item(item=session_id, partition_key=self.partition(principal, session_id))
        except CosmosResourceNotFoundError:
            return None
        return WorkspaceSession.model_validate(body)

    async def list_sessions(self, principal: Principal) -> list[WorkspaceSession]:
        query = (
            "SELECT * FROM c WHERE c.recordType = 'session' "
            "AND c.tenantId = @tenantId AND c.ownerObjectId = @ownerObjectId"
        )
        parameters: list[dict[str, object]] = [
            {"name": "@tenantId", "value": str(principal.tenant_id)},
            {"name": "@ownerObjectId", "value": str(principal.owner_object_id)},
        ]
        iterator = self._container.query_items(query=query, parameters=parameters)
        return sorted(
            [WorkspaceSession.model_validate(item) async for item in iterator],
            key=lambda session: session.last_activity_at, reverse=True,
        )

    async def rename_session(
        self, principal: Principal, session_id: str, title: str, expected_etag: str
    ) -> WorkspaceSession:
        session = await self.get_session(principal, session_id)
        if session is None:
            raise StorageConflict("workspace session is unavailable")
        updated = session.model_copy(update={"title": title, "last_activity_at": datetime.now(UTC)})
        try:
            document = await self._container.replace_item(
                item=session_id,
                body=updated.model_dump(mode="json", by_alias=True, exclude={"etag"}),
                etag=expected_etag,
                match_condition=MatchConditions.IfNotModified,
            )
        except CosmosHttpResponseError as error:
            if error.status_code == 412:
                raise StorageConflict("workspace session has changed") from error
            raise
        return WorkspaceSession.model_validate(document)

    async def delete_session_records(self, principal: Principal, session_id: str) -> None:
        partition_key = self.partition(principal, session_id)
        query = (
            "SELECT c.id FROM c WHERE c.tenantId = @tenantId AND c.ownerObjectId = @ownerObjectId "
            "AND c.sessionId = @sessionId"
        )
        parameters: list[dict[str, object]] = [
            {"name": "@tenantId", "value": str(principal.tenant_id)},
            {"name": "@ownerObjectId", "value": str(principal.owner_object_id)},
            {"name": "@sessionId", "value": session_id},
        ]
        raw_iterator = self._container.query_items(query=query, parameters=parameters, partition_key=partition_key)
        iterator = cast(AsyncIterator[dict[str, object]], raw_iterator)
        item_ids: list[str] = []
        async for item in iterator:
            item_id = item.get("id")
            if isinstance(item_id, str):
                item_ids.append(item_id)
        for start in range(0, len(item_ids), 100):
            batch_operations = [("delete", (item_id,)) for item_id in item_ids[start : start + 100]]
            await self._container.execute_item_batch(batch_operations=batch_operations, partition_key=partition_key)
