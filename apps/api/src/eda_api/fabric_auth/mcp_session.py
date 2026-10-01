"""Authenticated MCP sessions over streamable HTTP.

Fabric's hosted MCP endpoints (the ontology endpoint and a KQL database's
endpoint) take the signed-in user's own token, so a session belongs to one
caller. `open_streamable_http_session` opens one for one use; `McpSessionPool`
holds initialized sessions open for reuse, keyed by the exact token, so nothing
is ever shared between principals.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Protocol

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

# The caller bounds the whole call with its own deadline; this only stops a silent connection.
MCP_READ_TIMEOUT_SECONDS = 120.0
# A held session is closed after this long unused; a user's token outlives it by far.
SESSION_IDLE_SECONDS = 300.0
MAX_HELD_SESSIONS = 32


class McpSession(Protocol):
    async def initialize(self) -> object: ...

    async def list_tools(self) -> object: ...

    async def call_tool(self, name: str, arguments: dict[str, object]) -> object: ...


type SessionOpener = Callable[[str, str], AbstractAsyncContextManager[McpSession]]


@asynccontextmanager
async def open_streamable_http_session(url: str, bearer_token: str) -> AsyncGenerator[McpSession]:
    timeout = httpx.Timeout(MCP_READ_TIMEOUT_SECONDS, connect=10)
    async with httpx.AsyncClient(
        headers={"Authorization": "Bearer " + bearer_token},
        follow_redirects=False,
        timeout=timeout,
    ) as http_client:
        async with streamable_http_client(url, http_client=http_client) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                yield session


class _HeldSession:
    """One initialized session, owned by its own task so it is opened and closed in the same task."""

    def __init__(self, opener: SessionOpener, url: str, bearer_token: str, now: float) -> None:
        self.last_used = now
        self.uses = 0
        self._ready = asyncio.Event()
        self._closing = asyncio.Event()
        self._session: McpSession | None = None
        self._error: BaseException | None = None
        self._task = asyncio.create_task(self._hold(opener, url, bearer_token))

    async def _hold(self, opener: SessionOpener, url: str, bearer_token: str) -> None:
        try:
            async with opener(url, bearer_token) as session:
                await session.initialize()
                self._session = session
                self._ready.set()
                await self._closing.wait()
        except Exception as error:
            self._error = error
        finally:
            self._session = None
            self._ready.set()

    async def session(self) -> McpSession:
        await self._ready.wait()
        if self._error is not None:
            raise self._error
        if self._session is None or self._closing.is_set():
            raise RuntimeError("the MCP session closed before it could be used")
        return self._session

    def close(self) -> None:
        self._closing.set()

    async def wait_closed(self) -> None:
        self.close()
        with contextlib.suppress(Exception):
            await self._task


class McpSessionPool:
    """Initialized sessions held open per endpoint and exact bearer token.

    Measured against a KQL database's endpoint on 2026-10-01: the first
    executeQuery on a new session took 4 to 6 seconds, each later one about 1.6.
    Keying by the token itself keeps every session with the principal whose token
    opened it, and a refreshed token opens a new session instead of reusing one
    that is about to be refused. A call that fails discards its session; when the
    session had already served a call, the operation gets one fresh session, since
    a held session can expire on the server. Only pass operations that are safe to
    repeat.
    """

    def __init__(
        self,
        opener: SessionOpener | None = None,
        *,
        idle_seconds: float = SESSION_IDLE_SECONDS,
        max_sessions: int = MAX_HELD_SESSIONS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._opener = opener or open_streamable_http_session
        self._idle_seconds = idle_seconds
        self._max_sessions = max(1, max_sessions)
        self._clock = clock
        self._held: dict[tuple[str, str], _HeldSession] = {}

    @property
    def held(self) -> int:
        return len(self._held)

    async def run[T](self, url: str, bearer_token: str, operation: Callable[[McpSession], Awaitable[T]]) -> T:
        key = (url, hashlib.sha256(bearer_token.encode()).hexdigest())
        for attempt in (1, 2):
            held = self._acquire(key, url, bearer_token)
            reused = held.uses > 0
            try:
                result = await operation(await held.session())
            except Exception:
                self._discard(key, held)
                if reused and attempt == 1:
                    continue
                raise
            except BaseException:
                # An abandoned call may leave the transport half-used; the next call starts clean.
                self._discard(key, held)
                raise
            held.uses += 1
            held.last_used = self._clock()
            return result
        raise RuntimeError("unreachable")

    def _discard(self, key: tuple[str, str], held: _HeldSession) -> None:
        if self._held.get(key) is held:
            del self._held[key]
        held.close()

    def _acquire(self, key: tuple[str, str], url: str, bearer_token: str) -> _HeldSession:
        now = self._clock()
        for stale in [item for item, held in self._held.items() if now - held.last_used >= self._idle_seconds]:
            self._held.pop(stale).close()
        held = self._held.get(key)
        if held is None:
            while len(self._held) >= self._max_sessions:
                oldest = min(self._held, key=lambda item: self._held[item].last_used)
                self._held.pop(oldest).close()
            held = _HeldSession(self._opener, url, bearer_token, now)
            self._held[key] = held
        held.last_used = now
        return held

    async def aclose(self) -> None:
        held, self._held = list(self._held.values()), {}
        await asyncio.gather(*(item.wait_closed() for item in held))
