"""Whether the configured source's Fabric capacity is running.

A paused capacity refuses every data-plane call with CapacityNotActive while the
link, the workspace and the item itself still read as healthy, so nothing else
the product shows can reveal it. The state belongs to the source rather than to
whoever asked, like the endpoint's tool contract, so one observation serves every
caller and carries no customer data.

Every real read reports what it saw. When nothing recent is known, one probe opens
the ontology endpoint and runs no query: a paused capacity refuses the handshake
itself (about two seconds, measured), and a running one answers it without
spending a query.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Final, Protocol, cast
from uuid import UUID

from eda_fabric_auth.msal_cache import FabricAuthorizationRequired

logger = logging.getLogger(__name__)

CAPACITY_NOT_ACTIVE: Final = "CapacityNotActive"
# A running capacity is rarely paused mid-session, so an active reading holds for a minute,
ACTIVE_FRESH_SECONDS: Final = 60.0
# while a paused one is read again sooner, so resuming the capacity shows up quickly.
PAUSED_FRESH_SECONDS: Final = 20.0
PROBE_TIMEOUT_SECONDS: Final = 10.0

type CapacityProbe = Callable[[], Awaitable[None]]


class CapacityState(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    UNKNOWN = "unknown"


def capacity_paused(error: BaseException) -> bool:
    """True when Fabric refused a call because the source's capacity is not running."""
    seen: set[int] = set()
    pending: list[BaseException] = [error]
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if CAPACITY_NOT_ACTIVE in str(current) or _error_code(current) == CAPACITY_NOT_ACTIVE:
            return True
        if isinstance(current, BaseExceptionGroup):
            pending.extend(cast(BaseExceptionGroup[BaseException], current).exceptions)
        pending.extend(linked for linked in (current.__cause__, current.__context__) if linked is not None)
    return False


def _error_code(error: BaseException) -> object:
    # The MCP client keeps Fabric's own code in the JSON-RPC error data.
    data: object = getattr(getattr(error, "error", None), "data", None)
    return cast(dict[str, object], data).get("errorCode") if isinstance(data, dict) else None


class CapacityMonitor:
    """The source's capacity state as this process last saw it, shared by every caller."""

    def __init__(
        self,
        *,
        active_fresh_seconds: float = ACTIVE_FRESH_SECONDS,
        paused_fresh_seconds: float = PAUSED_FRESH_SECONDS,
        probe_timeout_seconds: float = PROBE_TIMEOUT_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._active_fresh_seconds = active_fresh_seconds
        self._paused_fresh_seconds = paused_fresh_seconds
        self._probe_timeout_seconds = probe_timeout_seconds
        self._clock = clock
        self._state = CapacityState.UNKNOWN
        self._observed_at: float | None = None
        self._probe: asyncio.Task[CapacityState] | None = None

    def record(self, state: CapacityState) -> None:
        if state is CapacityState.UNKNOWN:
            return
        self._state = state
        self._observed_at = self._clock()

    def observe_failure(self, error: BaseException) -> None:
        """Most failures say nothing about the capacity; only Fabric's own refusal does."""
        if capacity_paused(error):
            self.record(CapacityState.PAUSED)

    def fresh(self) -> CapacityState | None:
        if self._observed_at is None:
            return None
        limit = self._active_fresh_seconds if self._state is CapacityState.ACTIVE else self._paused_fresh_seconds
        return self._state if self._clock() - self._observed_at < limit else None

    async def check(self, probe: CapacityProbe) -> CapacityState:
        """The fresh observation, or else one probe that every concurrent caller shares."""
        fresh = self.fresh()
        if fresh is not None:
            return fresh
        if self._probe is None or self._probe.done():
            self._probe = asyncio.ensure_future(self._run(probe))
        # A caller that goes away must not cancel the probe the others are waiting on.
        return await asyncio.shield(self._probe)

    async def _run(self, probe: CapacityProbe) -> CapacityState:
        try:
            async with asyncio.timeout(self._probe_timeout_seconds):
                await probe()
        except TimeoutError:
            logger.warning("Fabric capacity probe timed out")
            return CapacityState.UNKNOWN
        except FabricAuthorizationRequired:
            return CapacityState.UNKNOWN
        except Exception as error:
            if capacity_paused(error):
                self.record(CapacityState.PAUSED)
                return CapacityState.PAUSED
            logger.warning("Fabric capacity probe failed: %s", type(error).__name__)
            return CapacityState.UNKNOWN
        self.record(CapacityState.ACTIVE)
        return CapacityState.ACTIVE


class OwnerTokenProvider(Protocol):
    async def acquire_for_owner(self, tenant_id: UUID, owner_object_id: UUID) -> str: ...


class CapacityHandshake(Protocol):
    async def probe_capacity(self, bearer_token: str) -> None: ...


class CapacityStatus:
    """Answers for one signed-in owner, probing with that owner's own Fabric grant."""

    def __init__(self, monitor: CapacityMonitor, *, tokens: OwnerTokenProvider, source: CapacityHandshake) -> None:
        self._monitor = monitor
        self._tokens = tokens
        self._source = source

    async def for_owner(self, tenant_id: UUID, owner_object_id: UUID) -> CapacityState:
        async def probe() -> None:
            bearer_token = await self._tokens.acquire_for_owner(tenant_id, owner_object_id)
            if not bearer_token:
                raise FabricAuthorizationRequired("Fabric owner token is required")
            await self._source.probe_capacity(bearer_token)

        return await self._monitor.check(probe)
