from __future__ import annotations

from datetime import UTC, datetime
from threading import Event
from typing import Any, cast

from eda_runtime_state.redis_auth import create_redis_credential_provider
from redis.auth.idp import IdentityProviderInterface
from redis.auth.token import SimpleToken, TokenInterface
from redis.auth.token_manager import RetryPolicy, TokenManagerConfig
from redis_entraid.cred_provider import EntraIdCredentialsProvider
from redis_entraid.identity_provider import ManagedIdentityIdType, ManagedIdentityType


def test_redis_credential_provider_uses_user_assigned_managed_identity() -> None:
    calls: list[dict[str, Any]] = []
    expected_provider = cast(EntraIdCredentialsProvider, object())

    def create_provider(**kwargs: Any) -> EntraIdCredentialsProvider:
        calls.append(kwargs)
        return expected_provider

    provider = create_redis_credential_provider(
        "33333333-3333-3333-3333-333333333333",
        provider_factory=create_provider,
    )

    assert provider is expected_provider
    assert calls == [
        {
            "identity_type": ManagedIdentityType.USER_ASSIGNED,
            "resource": "https://redis.azure.com/",
            "id_type": ManagedIdentityIdType.CLIENT_ID,
            "id_value": "33333333-3333-3333-3333-333333333333",
        }
    ]


def test_redis_credential_provider_uses_system_assigned_identity_when_client_id_is_absent() -> None:
    calls: list[dict[str, Any]] = []
    expected_provider = cast(EntraIdCredentialsProvider, object())

    def create_provider(**kwargs: Any) -> EntraIdCredentialsProvider:
        calls.append(kwargs)
        return expected_provider

    provider = create_redis_credential_provider(None, provider_factory=create_provider)

    assert provider is expected_provider
    assert calls == [
        {
            "identity_type": ManagedIdentityType.SYSTEM_ASSIGNED,
            "resource": "https://redis.azure.com/",
        }
    ]


class FakeIdentityProvider(IdentityProviderInterface):
    def __init__(self) -> None:
        self.calls = 0

    def request_token(self, force_refresh: bool = False) -> TokenInterface:
        del force_refresh
        self.calls += 1
        now_ms = datetime.now(UTC).timestamp() * 1000
        expires_at_ms = now_ms + (250 if self.calls < 3 else 10_000)
        return SimpleToken(f"token-{self.calls}", expires_at_ms, now_ms, {"oid": "managed-identity"})


def test_provider_refreshes_before_expiry_and_reauthenticates_on_reconnect() -> None:
    identity_provider = FakeIdentityProvider()
    provider = EntraIdCredentialsProvider(
        identity_provider,
        TokenManagerConfig(
            expiration_refresh_ratio=0.5,
            lower_refresh_bound_millis=0,
            token_request_execution_timeout_in_ms=100,
            retry_policy=RetryPolicy(max_attempts=1, delay_in_ms=1),
        ),
    )
    refreshed = Event()
    provider.on_next(lambda _token: refreshed.set())  # pyright: ignore[reportUnknownMemberType]

    assert provider.get_credentials() == ("managed-identity", "token-1")
    assert refreshed.wait(timeout=1)
    assert identity_provider.calls >= 3

    assert provider.get_credentials() == ("managed-identity", "token-4")
    assert identity_provider.calls == 4
