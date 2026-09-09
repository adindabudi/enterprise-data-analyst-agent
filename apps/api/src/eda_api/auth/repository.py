from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol

from azure.core import MatchConditions
from azure.cosmos.aio import ContainerProxy
from azure.cosmos.exceptions import CosmosHttpResponseError, CosmosResourceNotFoundError

from .models import AuthFlowRecord, AuthSessionRecord


class AuthRepository(Protocol):
    async def put_flow(self, flow: AuthFlowRecord) -> None: ...

    async def pop_flow(self, flow_id: str) -> AuthFlowRecord | None: ...

    async def put_session(self, session: AuthSessionRecord) -> None: ...

    async def get_session(self, session_id: str) -> AuthSessionRecord | None: ...

    async def delete_session(self, session_id: str) -> None: ...


class InMemoryAuthRepository:
    def __init__(self) -> None:
        self._flows: dict[str, AuthFlowRecord] = {}
        self._sessions: dict[str, AuthSessionRecord] = {}

    async def put_flow(self, flow: AuthFlowRecord) -> None:
        self._flows[flow.id] = flow

    async def pop_flow(self, flow_id: str) -> AuthFlowRecord | None:
        flow = self._flows.pop(flow_id, None)
        if flow is None or _is_expired(flow.expires_at):
            return None
        return flow

    async def put_session(self, session: AuthSessionRecord) -> None:
        self._sessions[session.id] = session

    async def get_session(self, session_id: str) -> AuthSessionRecord | None:
        session = self._sessions.get(session_id)
        if session is None or _is_expired(session.expires_at):
            return None
        return session

    async def delete_session(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)


class CosmosAuthRepository:
    def __init__(self, container: ContainerProxy) -> None:
        self._container = container

    async def put_flow(self, flow: AuthFlowRecord) -> None:
        await self._container.create_item(flow.to_document())

    async def pop_flow(self, flow_id: str) -> AuthFlowRecord | None:
        try:
            document = await self._container.read_item(item=flow_id, partition_key=flow_id)
        except CosmosResourceNotFoundError:
            return None

        etag = document.get("_etag")
        if not isinstance(etag, str):
            return None
        try:
            await self._container.delete_item(
                item=flow_id,
                partition_key=flow_id,
                etag=etag,
                match_condition=MatchConditions.IfNotModified,
            )
        except CosmosResourceNotFoundError:
            return None
        except CosmosHttpResponseError as error:
            if error.status_code == 412:
                return None
            raise

        flow = AuthFlowRecord.model_validate(_flow_values(document))
        if _is_expired(flow.expires_at):
            return None
        return flow

    async def put_session(self, session: AuthSessionRecord) -> None:
        await self._container.upsert_item(session.to_document())

    async def get_session(self, session_id: str) -> AuthSessionRecord | None:
        try:
            document = await self._container.read_item(item=session_id, partition_key=session_id)
        except CosmosResourceNotFoundError:
            return None

        session = AuthSessionRecord.model_validate(_session_values(document))
        if _is_expired(session.expires_at):
            return None
        return session

    async def delete_session(self, session_id: str) -> None:
        try:
            await self._container.delete_item(item=session_id, partition_key=session_id)
        except CosmosResourceNotFoundError:
            return None


def _is_expired(expires_at: datetime) -> bool:
    return expires_at <= datetime.now(UTC)


def _flow_values(document: dict[str, Any]) -> dict[str, Any]:
    return {key: document[key] for key in ("id", "recordType", "flow", "expiresAt")}


def _session_values(document: dict[str, Any]) -> dict[str, Any]:
    return {
        key: document[key]
        for key in ("id", "recordType", "tenantId", "ownerObjectId", "audience", "csrfSha256", "expiresAt")
    }
