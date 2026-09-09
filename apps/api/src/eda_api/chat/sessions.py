"""Owner-scoped persistence for interactive agent state and fetched query results."""

from __future__ import annotations

import json
import logging
from typing import Any, Protocol, cast

from azure.cosmos.aio import ContainerProxy
from azure.cosmos.exceptions import CosmosResourceNotFoundError
from eda_runtime_state.models import TaskPartition

MAX_SESSION_STATE_CHARS = 512_000
logger = logging.getLogger(__name__)


class InteractiveSessionStore(Protocol):
    async def load(self, partition: TaskPartition) -> dict[str, Any] | None: ...

    async def save(self, partition: TaskPartition, state: dict[str, Any]) -> None: ...


class CosmosInteractiveSessionStore:
    def __init__(self, workspace: ContainerProxy, *, fallback: InteractiveSessionStore | None = None) -> None:
        self._workspace = workspace
        self._fallback = fallback

    async def load(self, partition: TaskPartition) -> dict[str, Any] | None:
        try:
            document = await self._workspace.read_item(item="interactive-session", partition_key=partition.values())
        except CosmosResourceNotFoundError:
            try:
                cached = await self._fallback.load(partition) if self._fallback is not None else None
            except Exception:
                logger.warning("legacy interactive session cache is unavailable")
                cached = None
            if cached is None:
                return None
            return await self._save_state(partition, cached)
        state = document.get("state")
        return cast(dict[str, Any], state) if isinstance(state, dict) else None

    async def save(self, partition: TaskPartition, state: dict[str, Any]) -> None:
        await self._save_state(partition, state)

    async def _save_state(self, partition: TaskPartition, state: dict[str, Any]) -> dict[str, Any] | None:
        serialized = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
        stored_state = state if len(serialized.encode("utf-8")) <= MAX_SESSION_STATE_CHARS else None
        if stored_state is None:
            logger.warning("interactive session state exceeds the persistence budget")
        await self._workspace.upsert_item(body={
            "id": "interactive-session", "recordType": "interactiveSession",
            "tenantId": str(partition.tenant_id), "ownerObjectId": str(partition.owner_object_id),
            "sessionId": partition.session_id, "ttl": 30 * 24 * 60 * 60,
            "state": stored_state,
        })
        return stored_state


class RedisInteractiveSessionStore:
    def __init__(self, redis: Any, *, ttl_seconds: int) -> None:
        if ttl_seconds <= 0:
            raise ValueError("interactive session TTL must be positive")
        self._redis = redis
        self._ttl_seconds = ttl_seconds

    async def load(self, partition: TaskPartition) -> dict[str, Any] | None:
        raw = await self._redis.get(_key(partition))
        if not raw:
            return None
        text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            return None
        return cast(dict[str, Any], value) if isinstance(value, dict) else None

    async def save(self, partition: TaskPartition, state: dict[str, Any]) -> None:
        serialized = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
        # A session that outgrows the budget is dropped rather than trimmed blindly, so the
        # next turn rebuilds from the user's history instead of resuming a mangled transcript.
        if len(serialized) > MAX_SESSION_STATE_CHARS:
            await self._redis.delete(_key(partition))
            return
        await self._redis.set(_key(partition), serialized, ex=self._ttl_seconds)


def _key(partition: TaskPartition) -> str:
    return f"chat:session:{partition.tenant_id}:{partition.owner_object_id}:{partition.session_id}"
