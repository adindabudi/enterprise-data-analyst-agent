from __future__ import annotations

from uuid import UUID

import pytest
from azure.core.credentials import AccessToken
from eda_fabric_auth import FabricProvider
from eda_worker.fabric.contracts import FabricPrincipal
from eda_worker.fabric.token_provider import FabricAccessTokenProvider


class _FakeSilentService:
    def __init__(self) -> None:
        self.calls: list[tuple[UUID, UUID, FabricProvider]] = []

    async def acquire_access_token(
        self, *, tenant_id: UUID, owner_object_id: UUID, provider: FabricProvider
    ) -> AccessToken:
        self.calls.append((tenant_id, owner_object_id, provider))
        return AccessToken(token="fabric-token", expires_on=1_900_000_000)  # noqa: S106 -- synthetic test value


@pytest.mark.asyncio
async def test_token_provider_delegates_with_provider_bound_principal() -> None:
    provider = FabricAccessTokenProvider.__new__(FabricAccessTokenProvider)
    fake_service = _FakeSilentService()
    provider._silent_service = fake_service  # type: ignore[attr-defined]

    principal = FabricPrincipal(
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
        audience=UUID("33333333-3333-3333-3333-333333333333"),
    )

    token = await provider.acquire_for_principal(principal=principal, provider=FabricProvider.ONTOLOGY)

    assert token.token == "fabric-token"  # noqa: S105 -- synthetic test value
    assert fake_service.calls == [
        (
            UUID("11111111-1111-1111-1111-111111111111"),
            UUID("22222222-2222-2222-2222-222222222222"),
            FabricProvider.ONTOLOGY,
        )
    ]
