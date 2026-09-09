from __future__ import annotations

from typing import Any

import pytest
from eda_api.auth.msal_client import MsalAuthClient
from eda_api.config import Settings


class FakeMsalApplication:
    def __init__(self) -> None:
        self.initiated: dict[str, Any] = {}

    def initiate_auth_code_flow(self, scopes: list[str], **kwargs: Any) -> dict[str, Any]:
        self.initiated = {"scopes": scopes, **kwargs}
        return {
            "state": "state-12345678",
            "code_verifier": "verifier",
            "auth_uri": "https://login.microsoftonline.com/authorize",
        }

    def acquire_token_by_auth_code_flow(self, flow: dict[str, Any], response: dict[str, str]) -> dict[str, Any]:
        assert flow["state"] == response["state"]
        return {
            "id_token_claims": {
                "tid": "11111111-1111-1111-1111-111111111111",
                "oid": "22222222-2222-2222-2222-222222222222",
                "aud": "33333333-3333-3333-3333-333333333333",
            },
            "access_token": "discard-me",
        }


@pytest.mark.asyncio
async def test_login_uses_pkce_form_post_without_offline_scope(settings: Settings) -> None:
    application = FakeMsalApplication()
    client = MsalAuthClient(settings, application_factory=lambda: application)

    flow = await client.initiate()

    assert flow["code_verifier"] == "verifier"
    assert application.initiated == {
        "scopes": [],
        "redirect_uri": settings.redirect_uri,
        "response_mode": "form_post",
    }


@pytest.mark.asyncio
async def test_completion_returns_only_validated_principal(settings: Settings) -> None:
    application = FakeMsalApplication()
    client = MsalAuthClient(settings, application_factory=lambda: application)

    principal = await client.complete(
        {"state": "state-12345678"},
        {"state": "state-12345678", "code": "one-time-code"},
    )

    assert str(principal.owner_object_id) == "22222222-2222-2222-2222-222222222222"
    assert not hasattr(principal, "access_token")
