from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any

import anyio
import pytest
from eda_worker.fabric.token_provider import FabricAccessTokenProvider


@pytest.mark.asyncio
async def test_worker_token_provider_offload_uses_injected_limiter() -> None:
    captured: list[anyio.CapacityLimiter] = []

    async def recording_run_sync(func: Callable[[], Any], *, limiter: anyio.CapacityLimiter) -> Any:
        captured.append(limiter)
        return func()

    provider = FabricAccessTokenProvider.__new__(FabricAccessTokenProvider)
    provider._limiter = anyio.CapacityLimiter(3)  # type: ignore[attr-defined]
    provider._run_sync = recording_run_sync  # type: ignore[attr-defined]

    result = await provider._offload(lambda: "ok")  # type: ignore[attr-defined]

    assert result == "ok"
    assert captured == [provider._limiter]  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_worker_token_provider_offload_keeps_event_loop_responsive() -> None:
    provider = FabricAccessTokenProvider.__new__(FabricAccessTokenProvider)
    provider._limiter = anyio.CapacityLimiter(1)  # type: ignore[attr-defined]
    provider._run_sync = anyio.to_thread.run_sync  # type: ignore[attr-defined]

    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        deadline = asyncio.get_running_loop().time() + 0.08
        while asyncio.get_running_loop().time() < deadline:
            ticks += 1
            await asyncio.sleep(0.005)

    await asyncio.gather(
        ticker(),
        provider._offload(lambda: time.sleep(0.05)),  # type: ignore[attr-defined]
    )

    assert ticks > 2
