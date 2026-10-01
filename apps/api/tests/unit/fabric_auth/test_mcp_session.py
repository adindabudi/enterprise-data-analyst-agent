"""Held MCP sessions: reused per endpoint and exact token, never across principals, discarded on failure."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

import pytest
from eda_api.fabric_auth.mcp_session import McpSessionPool


class Session:
    def __init__(self, number: int) -> None:
        self.number = number
        self.initialized = False

    async def initialize(self) -> None:
        self.initialized = True

    async def list_tools(self) -> list[object]:
        return []

    async def call_tool(self, name: str, arguments: dict[str, object]) -> object:
        del name, arguments
        return self.number


class Opener:
    def __init__(self) -> None:
        self.opened: list[tuple[str, str]] = []
        self.closed: list[int] = []
        self.refuse = False

    @asynccontextmanager
    async def __call__(self, url: str, bearer_token: str):
        self.opened.append((url, bearer_token))
        if self.refuse:
            raise RuntimeError("401 Unauthorized")
        session = Session(len(self.opened))
        try:
            yield session
        finally:
            self.closed.append(session.number)


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


async def _call(session: Any) -> int:
    assert session.initialized
    return await session.call_tool("executeQuery", {})


@pytest.mark.asyncio
async def test_a_session_is_opened_once_and_reused_for_the_same_token() -> None:
    opener = Opener()
    pool = McpSessionPool(opener)

    first = await pool.run("https://endpoint", "token-a", _call)
    second = await pool.run("https://endpoint", "token-a", _call)
    await pool.aclose()

    assert first == second == 1
    assert opener.opened == [("https://endpoint", "token-a")]
    assert opener.closed == [1]


@pytest.mark.asyncio
async def test_another_principals_token_never_reuses_a_session() -> None:
    opener = Opener()
    pool = McpSessionPool(opener)

    first = await pool.run("https://endpoint", "token-a", _call)
    second = await pool.run("https://endpoint", "token-b", _call)
    await pool.aclose()

    assert (first, second) == (1, 2)
    assert [token for _, token in opener.opened] == ["token-a", "token-b"]


@pytest.mark.asyncio
async def test_an_idle_session_is_closed_and_replaced() -> None:
    opener, clock = Opener(), Clock()
    pool = McpSessionPool(opener, idle_seconds=300, clock=clock)

    await pool.run("https://endpoint", "token-a", _call)
    clock.now = 301
    second = await pool.run("https://endpoint", "token-a", _call)
    await asyncio.sleep(0)
    await pool.aclose()

    assert second == 2 and 1 in opener.closed


@pytest.mark.asyncio
async def test_a_failed_call_on_a_held_session_gets_one_fresh_session() -> None:
    opener = Opener()
    pool = McpSessionPool(opener)
    await pool.run("https://endpoint", "token-a", _call)
    attempts: list[int] = []

    async def expires_once(session: Any) -> int:
        attempts.append(session.number)
        if session.number == 1:
            raise RuntimeError("session expired")
        return session.number

    result = await pool.run("https://endpoint", "token-a", expires_once)
    await pool.aclose()

    assert attempts == [1, 2] and result == 2


@pytest.mark.asyncio
async def test_a_failure_on_a_new_session_is_reported_not_retried() -> None:
    opener = Opener()
    opener.refuse = True
    pool = McpSessionPool(opener)

    with pytest.raises(RuntimeError, match="401"):
        await pool.run("https://endpoint", "token-a", _call)

    assert len(opener.opened) == 1 and pool.held == 0


@pytest.mark.asyncio
async def test_the_pool_holds_at_most_its_bound() -> None:
    opener = Opener()
    pool = McpSessionPool(opener, max_sessions=2)

    for token in ("token-a", "token-b", "token-c"):
        await pool.run("https://endpoint", token, _call)
    held = pool.held
    await pool.aclose()

    assert held == 2 and 1 in opener.closed
