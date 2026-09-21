from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from eda_fabric_auth.crypto import CipherEnvelope
from eda_fabric_auth.models import FabricGrantRecord, FabricGrantState, FabricProvider
from eda_fabric_auth.msal_cache import (
    FABRIC_RESOURCE_AUDIENCE,
    SEMANTIC_MODEL_APPLICATION_SCOPES,
    FabricAuthorizationRequired,
    FabricMsalAuthorizationCodeService,
    FabricMsalSilentTokenService,
    audience_hash,
    create_confidential_client_application,
    create_serializable_token_cache,
    fabric_authority,
    provider_application_scopes,
    provider_scope_hash,
)
from eda_fabric_auth.repository import FabricGrantConflict, InMemoryFabricGrantRepository
from msal import SerializableTokenCache


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _access_token(
    *,
    tenant_id: str = "33333333-3333-3333-3333-333333333333",
    audience: str = FABRIC_RESOURCE_AUDIENCE,
    scopes: str = "Item.Execute.All Item.Read.All",
) -> str:
    header = _encode(b'{"alg":"RS256","typ":"JWT"}')
    claims = _encode(
        json.dumps(
            {
                "aud": audience,
                "exp": 4_102_444_800,
                "scp": scopes,
                "tid": tenant_id,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    )
    return f"{header}.{claims}.signature"


@dataclass
class _MutableNow:
    value: datetime

    def __call__(self) -> datetime:
        return self.value


class _FakeCipher:
    def __init__(self) -> None:
        self.decrypt_calls = 0
        self.encrypt_calls = 0

    async def decrypt(self, envelope: CipherEnvelope, context: Any) -> bytes:
        del context
        self.decrypt_calls += 1
        return base64.urlsafe_b64decode(envelope.ciphertext + "==")

    async def encrypt(self, plaintext: bytes, context: Any) -> CipherEnvelope:
        del context
        self.encrypt_calls += 1
        return CipherEnvelope(
            key_id="https://vault.example/keys/fabric-wrap/version-1",
            wrapped_dek="AA",
            nonce="AA",
            ciphertext=_encode(plaintext),
        )


class _NoDecryptCipher(_FakeCipher):
    async def decrypt(self, envelope: CipherEnvelope, context: Any) -> bytes:
        del envelope, context
        raise AssertionError("decrypt should not be called")


class _FakeCache:
    def __init__(self, initial: str = "") -> None:
        self.state = initial

    def serialize(self) -> str:
        return self.state

    def deserialize(self, state: str) -> None:
        self.state = state


class _FakeMsalApp:
    def __init__(
        self,
        *,
        cache: _FakeCache,
        mutate_cache: bool,
        result: dict[str, Any] | None,
        accounts: list[dict[str, Any]],
    ) -> None:
        self.cache = cache
        self.mutate_cache = mutate_cache
        self.result = result
        self.accounts = accounts
        self.initiated: dict[str, Any] | None = None

    def initiate_auth_code_flow(self, scopes: list[str], **kwargs: Any) -> dict[str, Any]:
        self.initiated = {"scopes": scopes, **kwargs}
        return {
            "state": "state-12345678",
            "nonce": "nonce-12345678",
            "code_verifier": "pkce",
            "auth_uri": "https://login.microsoftonline.com/authorize",
        }

    def acquire_token_by_auth_code_flow(self, flow: dict[str, Any], auth_response: dict[str, str]) -> dict[str, Any]:
        assert flow["state"] == auth_response["state"]
        return {
            "id_token_claims": {"tid": "33333333-3333-3333-3333-333333333333"},
            "access_token": "temporary",
        }

    def get_accounts(self, username: str | None = None) -> list[dict[str, Any]]:
        del username
        return self.accounts

    def acquire_token_silent(self, scopes: list[str], account: dict[str, Any]) -> dict[str, Any] | None:
        del account
        if self.mutate_cache:
            self.cache.state = self.cache.state + "|mutated"
        if self.result is None:
            return None
        return {**self.result, "scope": " ".join(scopes)}


@dataclass
class _AppFactoryState:
    authority: str | None = None
    client_id: str | None = None
    last_cache: _FakeCache | None = None
    app: _FakeMsalApp | None = None


def _build_grant(
    *, provider: FabricProvider, cache_bytes: bytes, state: FabricGrantState = FabricGrantState.LINKED
) -> FabricGrantRecord:
    now = datetime.now(UTC)
    return FabricGrantRecord(
        id=f"fabric-grant:{provider.value}",
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
        provider=provider,
        fabric_tenant_id=UUID("33333333-3333-3333-3333-333333333333"),
        account_hash="d" * 64,
        scope_hash=provider_scope_hash(provider),
        audience_hash=audience_hash(),
        state=state,
        cache=CipherEnvelope(
            key_id="https://vault.example/keys/fabric-wrap/version-1",
            wrapped_dek="AA",
            nonce="AA",
            ciphertext=_encode(cache_bytes),
        ),
        last_used_at=now,
        expires_at=now + timedelta(hours=1),
    )


async def _direct_offload(func: Any, /, *args: Any) -> Any:
    return func(*args)


def test_authority_and_provider_scopes_match_contract() -> None:
    assert fabric_authority(UUID("33333333-3333-3333-3333-333333333333")) == (
        "https://login.microsoftonline.com/33333333-3333-3333-3333-333333333333"
    )
    assert provider_application_scopes(FabricProvider.SEMANTIC_MODEL) == SEMANTIC_MODEL_APPLICATION_SCOPES
    assert provider_application_scopes(FabricProvider.ONTOLOGY) == (
        "https://analysis.windows.net/powerbi/api/Item.Read.All",
        "https://analysis.windows.net/powerbi/api/Item.Execute.All",
    )


def test_default_cache_factory_uses_msal_serializable_cache() -> None:
    assert isinstance(create_serializable_token_cache(), SerializableTokenCache)


def test_default_application_factory_preserves_oidc_and_refresh_scopes(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def application(*args: Any, **kwargs: Any) -> object:
        captured["args"] = args
        captured["kwargs"] = kwargs
        if "openid" in kwargs.get("exclude_scopes", []):
            raise ValueError('You can not opt out "openid" scope')
        return object()

    monkeypatch.setattr("eda_fabric_auth.msal_cache.ConfidentialClientApplication", application)

    result = create_confidential_client_application(
        client_id="44444444-4444-4444-4444-444444444444",
        authority="https://login.microsoftonline.com/33333333-3333-3333-3333-333333333333",
        client_assertion_callback=lambda: "assertion",
        token_cache=SerializableTokenCache(),
    )

    assert result is not None
    assert "exclude_scopes" not in captured["kwargs"]


@pytest.mark.asyncio
async def test_initiate_uses_exact_scopes_and_form_post_without_reserved_scopes() -> None:
    app_state = _AppFactoryState()

    def app_factory(
        *,
        client_id: str,
        authority: str,
        client_assertion_callback: Any,
        token_cache: Any,
    ) -> _FakeMsalApp:
        del client_assertion_callback
        app_state.authority = authority
        app_state.client_id = client_id
        app_state.last_cache = token_cache
        app_state.app = _FakeMsalApp(cache=token_cache, mutate_cache=False, result={}, accounts=[])
        return app_state.app

    service = FabricMsalAuthorizationCodeService(
        fabric_tenant_id=UUID("33333333-3333-3333-3333-333333333333"),
        fabric_client_id=UUID("44444444-4444-4444-4444-444444444444"),
        client_assertion_callback=lambda: "assertion",
        cipher=_FakeCipher(),  # type: ignore[arg-type]
        offload=_direct_offload,
        token_cache_factory=lambda: _FakeCache(),
        application_factory=app_factory,
    )

    await service.initiate_authorization_flow(
        provider=FabricProvider.SEMANTIC_MODEL,
        redirect_uri="https://app.example/api/fabric/auth/callback",
    )

    assert app_state.authority == "https://login.microsoftonline.com/33333333-3333-3333-3333-333333333333"
    assert app_state.client_id == "44444444-4444-4444-4444-444444444444"
    assert app_state.app is not None
    assert app_state.app.initiated == {
        "scopes": list(SEMANTIC_MODEL_APPLICATION_SCOPES),
        "redirect_uri": "https://app.example/api/fabric/auth/callback",
        "response_mode": "form_post",
    }
    scopes = app_state.app.initiated["scopes"]
    assert "offline_access" not in scopes
    assert "openid" not in scopes
    assert "profile" not in scopes


@pytest.mark.asyncio
async def test_complete_returns_pending_grant_only() -> None:
    service = FabricMsalAuthorizationCodeService(
        fabric_tenant_id=UUID("33333333-3333-3333-3333-333333333333"),
        fabric_client_id=UUID("44444444-4444-4444-4444-444444444444"),
        client_assertion_callback=lambda: "assertion",
        cipher=_FakeCipher(),  # type: ignore[arg-type]
        offload=_direct_offload,
        token_cache_factory=lambda: _FakeCache("seed"),
        application_factory=lambda **kwargs: _FakeMsalApp(
            cache=kwargs["token_cache"],
            mutate_cache=False,
            result={},
            accounts=[{"home_account_id": "home-account-id"}],
        ),
    )

    pending = await service.complete_authorization_flow(
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
        provider=FabricProvider.SEMANTIC_MODEL,
        flow={"state": "state-12345678", "nonce": "nonce-12345678"},
        auth_response={"state": "state-12345678", "code": "one-time-code"},
    )

    assert pending.provider is FabricProvider.SEMANTIC_MODEL
    assert pending.scope_hash == provider_scope_hash(FabricProvider.SEMANTIC_MODEL)
    assert pending.audience_hash == audience_hash()


@pytest.mark.asyncio
async def test_silent_success_unchanged_cache_persists_last_use_and_renewal() -> None:
    repository = InMemoryFabricGrantRepository()
    now = _MutableNow(datetime.now(UTC))
    grant = await repository.create_grant(
        _build_grant(provider=FabricProvider.SEMANTIC_MODEL, cache_bytes=b"cache-seed")
    )
    assert grant.etag is not None

    app_state = _AppFactoryState()

    def app_factory(**kwargs: Any) -> _FakeMsalApp:
        app_state.authority = kwargs["authority"]
        app_state.last_cache = kwargs["token_cache"]
        app_state.app = _FakeMsalApp(
            cache=kwargs["token_cache"],
            mutate_cache=False,
            result={
                "access_token": _access_token(),
                "expires_in": 1200,
            },
            accounts=[{"home_account_id": "home-account-id"}],
        )
        return app_state.app

    cipher = _FakeCipher()
    service = FabricMsalSilentTokenService(
        repository=repository,
        cipher=cipher,  # type: ignore[arg-type]
        fabric_tenant_id=UUID("33333333-3333-3333-3333-333333333333"),
        fabric_client_id=UUID("44444444-4444-4444-4444-444444444444"),
        client_assertion_callback=lambda: "assertion",
        offload=_direct_offload,
        token_cache_factory=lambda: _FakeCache(),
        application_factory=app_factory,
        now=now,
    )

    token = await service.acquire_access_token(
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
        provider=FabricProvider.SEMANTIC_MODEL,
    )

    assert token.token == _access_token()
    updated = await repository.get_grant(
        UUID("11111111-1111-1111-1111-111111111111"),
        UUID("22222222-2222-2222-2222-222222222222"),
        FabricProvider.SEMANTIC_MODEL,
    )
    assert updated is not None
    assert updated.last_used_at == now.value
    assert updated.expires_at == now.value + timedelta(days=30)
    assert updated.cache == grant.cache
    assert cipher.encrypt_calls == 0


@pytest.mark.asyncio
async def test_silent_success_changed_cache_reencrypts() -> None:
    repository = InMemoryFabricGrantRepository()
    await repository.create_grant(_build_grant(provider=FabricProvider.ONTOLOGY, cache_bytes=b"cache-seed"))

    service = FabricMsalSilentTokenService(
        repository=repository,
        cipher=_FakeCipher(),  # type: ignore[arg-type]
        fabric_tenant_id=UUID("33333333-3333-3333-3333-333333333333"),
        fabric_client_id=UUID("44444444-4444-4444-4444-444444444444"),
        client_assertion_callback=lambda: "assertion",
        offload=_direct_offload,
        token_cache_factory=lambda: _FakeCache(),
        application_factory=lambda **kwargs: _FakeMsalApp(
            cache=kwargs["token_cache"],
            mutate_cache=True,
            result={
                "access_token": _access_token(scopes="Item.Execute.All Item.Read.All"),
                "expires_in": 1200,
            },
            accounts=[{"home_account_id": "home-account-id"}],
        ),
    )

    await service.acquire_access_token(
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
        provider=FabricProvider.ONTOLOGY,
    )
    updated = await repository.get_grant(
        UUID("11111111-1111-1111-1111-111111111111"),
        UUID("22222222-2222-2222-2222-222222222222"),
        FabricProvider.ONTOLOGY,
    )
    assert updated is not None
    assert (
        updated.cache.ciphertext
        != _build_grant(provider=FabricProvider.ONTOLOGY, cache_bytes=b"cache-seed").cache.ciphertext
    )


class _ConflictOnceRepository:
    def __init__(self, inner: InMemoryFabricGrantRepository) -> None:
        self.inner = inner
        self.did_conflict = False

    async def get_grant(
        self, tenant_id: UUID, owner_object_id: UUID, provider: FabricProvider
    ) -> FabricGrantRecord | None:
        return await self.inner.get_grant(tenant_id, owner_object_id, provider)

    async def replace_grant(self, grant: FabricGrantRecord, *, etag: str) -> FabricGrantRecord:
        if not self.did_conflict:
            self.did_conflict = True
            current = await self.inner.get_grant(grant.tenant_id, grant.owner_object_id, grant.provider)
            assert current is not None and current.etag is not None
            await self.inner.replace_grant(
                current.model_copy(update={"last_used_at": current.last_used_at}), etag=current.etag
            )
            raise FabricGrantConflict("grant ETag mismatch")
        return await self.inner.replace_grant(grant, etag=etag)


@pytest.mark.asyncio
async def test_silent_retries_one_etag_conflict() -> None:
    inner = InMemoryFabricGrantRepository()
    await inner.create_grant(_build_grant(provider=FabricProvider.SEMANTIC_MODEL, cache_bytes=b"cache-seed"))

    service = FabricMsalSilentTokenService(
        repository=_ConflictOnceRepository(inner),  # type: ignore[arg-type]
        cipher=_FakeCipher(),  # type: ignore[arg-type]
        fabric_tenant_id=UUID("33333333-3333-3333-3333-333333333333"),
        fabric_client_id=UUID("44444444-4444-4444-4444-444444444444"),
        client_assertion_callback=lambda: "assertion",
        offload=_direct_offload,
        token_cache_factory=lambda: _FakeCache(),
        application_factory=lambda **kwargs: _FakeMsalApp(
            cache=kwargs["token_cache"],
            mutate_cache=False,
            result={
                "access_token": _access_token(),
                "expires_in": 1200,
            },
            accounts=[{"home_account_id": "home-account-id"}],
        ),
    )

    token = await service.acquire_access_token(
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
        provider=FabricProvider.SEMANTIC_MODEL,
    )
    assert token.token == _access_token()


@pytest.mark.asyncio
@pytest.mark.parametrize("grant_change", ["metadata", "reauth", "deleted", "expired", "scope", "cache"])
@pytest.mark.parametrize("mutate_cache", [False, True])
async def test_metadata_contention_does_not_invalidate_an_unchanged_token(
    grant_change: str,
    mutate_cache: bool,
) -> None:
    class ContendedRepository(InMemoryFabricGrantRepository):
        async def replace_grant(self, grant: FabricGrantRecord, *, etag: str) -> FabricGrantRecord:
            current = await self.get_grant(grant.tenant_id, grant.owner_object_id, grant.provider)
            assert current is not None and current.etag is not None
            if grant_change == "deleted":
                await self.delete_grant(grant.tenant_id, grant.owner_object_id, grant.provider)
                raise FabricGrantConflict("grant does not exist")
            changes: dict[str, Any] = {"last_used_at": datetime.now(UTC)}
            if grant_change == "reauth":
                changes["state"] = FabricGrantState.REAUTH_REQUIRED
            elif grant_change == "expired":
                changes["expires_at"] = datetime.now(UTC) - timedelta(seconds=1)
            elif grant_change == "scope":
                changes["scope_hash"] = "a" * 64
            elif grant_change == "cache":
                changes["cache"] = current.cache.model_copy(
                    update={
                        "ciphertext": _encode(f"cache-{current.etag}".encode()),
                    }
                )
            await super().replace_grant(
                current.model_copy(update=changes),
                etag=current.etag,
            )
            raise FabricGrantConflict("grant ETag mismatch")

    repository = ContendedRepository()
    grant = await repository.create_grant(_build_grant(provider=FabricProvider.ONTOLOGY, cache_bytes=b"cache"))
    service = FabricMsalSilentTokenService(
        repository=repository,
        cipher=_FakeCipher(),  # type: ignore[arg-type]
        fabric_tenant_id=grant.fabric_tenant_id,
        fabric_client_id=UUID("44444444-4444-4444-4444-444444444444"),
        client_assertion_callback=lambda: "assertion",
        offload=_direct_offload,
        token_cache_factory=_FakeCache,
        application_factory=lambda **kwargs: _FakeMsalApp(
            cache=kwargs["token_cache"],
            mutate_cache=mutate_cache,
            result={"access_token": _access_token(), "expires_in": 1200},
            accounts=[{"home_account_id": "home-account-id"}],
        ),
    )

    if grant_change != "metadata" or mutate_cache:
        expected_error = FabricGrantConflict if grant_change in {"cache", "metadata"} else FabricAuthorizationRequired
        with pytest.raises(expected_error):
            await service.acquire_access_token(
                tenant_id=grant.tenant_id,
                owner_object_id=grant.owner_object_id,
                provider=grant.provider,
            )
    else:
        token = await service.acquire_access_token(
            tenant_id=grant.tenant_id,
            owner_object_id=grant.owner_object_id,
            provider=grant.provider,
        )
        assert token.token == _access_token()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("result", "accounts"),
    [
        ({"error": "invalid_grant", "error_description": "refresh_token=secret"}, [{"home_account_id": "a"}]),
        ({"error": "interaction_required"}, [{"home_account_id": "a"}]),
        ({"access_token": _access_token()}, []),
        (
            {"access_token": _access_token(tenant_id="99999999-9999-9999-9999-999999999999")},
            [{"home_account_id": "a"}],
        ),
        (
            {"access_token": _access_token(audience="https://wrong.example")},
            [{"home_account_id": "a"}],
        ),
        (
            {"access_token": _access_token(scopes="Item.Read.All")},
            [{"home_account_id": "a"}],
        ),
    ],
)
async def test_reauth_errors_mark_state_without_secret_leak(
    result: dict[str, Any],
    accounts: list[dict[str, Any]],
) -> None:
    repository = InMemoryFabricGrantRepository()
    await repository.create_grant(_build_grant(provider=FabricProvider.SEMANTIC_MODEL, cache_bytes=b"cache-seed"))

    service = FabricMsalSilentTokenService(
        repository=repository,
        cipher=_FakeCipher(),  # type: ignore[arg-type]
        fabric_tenant_id=UUID("33333333-3333-3333-3333-333333333333"),
        fabric_client_id=UUID("44444444-4444-4444-4444-444444444444"),
        client_assertion_callback=lambda: "assertion",
        offload=_direct_offload,
        token_cache_factory=lambda: _FakeCache(),
        application_factory=lambda **kwargs: _FakeMsalApp(
            cache=kwargs["token_cache"],
            mutate_cache=False,
            result=result,
            accounts=accounts,
        ),
    )

    with pytest.raises(FabricAuthorizationRequired) as error:
        await service.acquire_access_token(
            tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
            owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
            provider=FabricProvider.SEMANTIC_MODEL,
        )

    assert "access_token" not in str(error.value)
    assert "refresh_token" not in str(error.value)
    updated = await repository.get_grant(
        UUID("11111111-1111-1111-1111-111111111111"),
        UUID("22222222-2222-2222-2222-222222222222"),
        FabricProvider.SEMANTIC_MODEL,
    )
    assert updated is not None
    assert updated.state is FabricGrantState.REAUTH_REQUIRED


@pytest.mark.asyncio
async def test_reauth_status_returns_without_decrypting_cache() -> None:
    repository = InMemoryFabricGrantRepository()
    await repository.create_grant(
        _build_grant(
            provider=FabricProvider.ONTOLOGY,
            cache_bytes=b"cache-seed",
            state=FabricGrantState.REAUTH_REQUIRED,
        )
    )
    service = FabricMsalSilentTokenService(
        repository=repository,
        cipher=_NoDecryptCipher(),  # type: ignore[arg-type]
        fabric_tenant_id=UUID("33333333-3333-3333-3333-333333333333"),
        fabric_client_id=UUID("44444444-4444-4444-4444-444444444444"),
        client_assertion_callback=lambda: "assertion",
        offload=_direct_offload,
        token_cache_factory=lambda: _FakeCache(),
        application_factory=lambda **kwargs: _FakeMsalApp(
            cache=kwargs["token_cache"],
            mutate_cache=False,
            result=None,
            accounts=[],
        ),
    )

    with pytest.raises(FabricAuthorizationRequired):
        await service.acquire_access_token(
            tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
            owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
            provider=FabricProvider.ONTOLOGY,
        )
