from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any
from uuid import UUID

import anyio
import pytest
from azure.core.credentials import AccessToken
from eda_api.fabric_auth.msal_client import FabricAccessTokenClient, FabricAuthorizationCodeClient
from eda_fabric_auth import FabricProvider
from eda_runtime_state.models import TaskPartition


class _FakeService:
    def __init__(self, offload: Callable[..., Any]) -> None:
        self._offload = offload

    async def initiate_authorization_flow(self, *, provider: FabricProvider, redirect_uri: str) -> dict[str, Any]:
        del provider, redirect_uri

        def blocking() -> dict[str, Any]:
            time.sleep(0.05)
            return {"auth_uri": "https://login.microsoftonline.com/authorize"}

        return await self._offload(blocking)

    async def complete_authorization_flow(self, **kwargs: Any) -> Any:
        del kwargs
        return None


@pytest.mark.asyncio
async def test_fabric_api_adapter_passes_capacity_limiter_to_run_sync() -> None:
    captured: list[anyio.CapacityLimiter] = []

    async def recording_run_sync(func: Callable[[], Any], *, limiter: anyio.CapacityLimiter) -> Any:
        captured.append(limiter)
        return func()

    adapter = FabricAuthorizationCodeClient.__new__(FabricAuthorizationCodeClient)
    adapter._limiter = anyio.CapacityLimiter(2)  # type: ignore[attr-defined]
    adapter._run_sync = recording_run_sync  # type: ignore[attr-defined]
    adapter._service = _FakeService(adapter._offload)  # type: ignore[attr-defined]

    await adapter.initiate(provider=FabricProvider.SEMANTIC_MODEL, redirect_uri="https://app.example/callback")

    assert captured == [adapter._limiter]  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_fabric_api_adapter_keeps_event_loop_responsive() -> None:
    adapter = FabricAuthorizationCodeClient.__new__(FabricAuthorizationCodeClient)
    adapter._limiter = anyio.CapacityLimiter(1)  # type: ignore[attr-defined]
    adapter._run_sync = anyio.to_thread.run_sync  # type: ignore[attr-defined]
    adapter._service = _FakeService(adapter._offload)  # type: ignore[attr-defined]

    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        deadline = asyncio.get_running_loop().time() + 0.08
        while asyncio.get_running_loop().time() < deadline:
            ticks += 1
            await asyncio.sleep(0.005)

    await asyncio.gather(
        ticker(),
        adapter.initiate(provider=FabricProvider.SEMANTIC_MODEL, redirect_uri="https://app.example/callback"),
    )

    assert ticks > 2


@pytest.mark.asyncio
async def test_linked_token_adapter_binds_owner_partition_to_ontology_provider() -> None:
    calls: list[dict[str, object]] = []

    class SilentService:
        async def acquire_access_token(self, **kwargs: object) -> AccessToken:
            calls.append(kwargs)
            return AccessToken("linked-user-token", 9999999999)

    adapter = FabricAccessTokenClient.__new__(FabricAccessTokenClient)
    adapter._service = SilentService()  # type: ignore[attr-defined]
    partition = TaskPartition(
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
        session_id="ses_interactive_12345678",
    )

    token = await adapter.acquire(partition)

    assert token == "linked-user-token"  # noqa: S105 - synthetic test value
    assert calls == [
        {
            "tenant_id": partition.tenant_id,
            "owner_object_id": partition.owner_object_id,
            "provider": FabricProvider.ONTOLOGY,
        }
    ]
