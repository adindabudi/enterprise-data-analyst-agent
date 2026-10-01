from __future__ import annotations

import base64
import binascii
import hashlib
import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any, Protocol, TypeVar, cast
from uuid import UUID, uuid4

from azure.core.credentials import AccessToken
from msal import ConfidentialClientApplication, SerializableTokenCache

from .crypto import EnvelopeCipher, EnvelopeContext
from .models import (
    FabricGrantRecord,
    FabricGrantState,
    FabricPendingGrantRecord,
    FabricProvider,
)
from .repository import FabricGrantConflict, FabricGrantRepository
from .scopes import ONTOLOGY_BYO_SCOPES

FABRIC_RESOURCE_AUDIENCE = "https://analysis.windows.net/powerbi/api"


class FabricAuthorizationRequired(RuntimeError):
    """Raised when an owner must re-link Fabric authorization."""


class TokenCacheProtocol(Protocol):
    def serialize(self) -> str: ...

    def deserialize(self, state: str) -> None: ...


class MsalApplication(Protocol):
    def initiate_auth_code_flow(
        self,
        scopes: list[str],
        *,
        redirect_uri: str | None = None,
        response_mode: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]: ...

    def acquire_token_by_auth_code_flow(
        self,
        auth_code_flow: dict[str, Any],
        auth_response: dict[str, str],
        scopes: list[str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]: ...

    def get_accounts(self, username: str | None = None) -> list[dict[str, Any]]: ...

    def acquire_token_silent(self, scopes: list[str], account: dict[str, Any]) -> dict[str, Any] | None: ...


class TokenCacheFactory(Protocol):
    def __call__(self) -> TokenCacheProtocol: ...


class MsalApplicationFactory(Protocol):
    def __call__(
        self,
        *,
        client_id: str,
        authority: str,
        client_assertion_callback: Callable[[], str],
        token_cache: TokenCacheProtocol,
    ) -> MsalApplication: ...


T = TypeVar("T")


class SyncOffload(Protocol):
    async def __call__(self, func: Callable[..., T], /, *args: Any) -> T: ...


def fabric_authority(fabric_tenant_id: UUID) -> str:
    return f"https://login.microsoftonline.com/{fabric_tenant_id}"


def provider_application_scopes(provider: FabricProvider) -> tuple[str, ...]:
    del provider
    return ONTOLOGY_BYO_SCOPES


def provider_scope_hash(provider: FabricProvider) -> str:
    return _sha256_text(" ".join(provider_application_scopes(provider)))


def audience_hash() -> str:
    return _sha256_text(FABRIC_RESOURCE_AUDIENCE)


def create_serializable_token_cache() -> TokenCacheProtocol:
    return SerializableTokenCache()


def create_confidential_client_application(
    *,
    client_id: str,
    authority: str,
    client_assertion_callback: Callable[[], str],
    token_cache: TokenCacheProtocol,
) -> MsalApplication:
    return cast(
        MsalApplication,
        ConfidentialClientApplication(
            client_id=client_id,
            authority=authority,
            client_credential={"client_assertion": client_assertion_callback},
            token_cache=token_cache,
        ),
    )


class FabricMsalAuthorizationCodeService:
    def __init__(
        self,
        *,
        fabric_tenant_id: UUID,
        fabric_client_id: UUID,
        client_assertion_callback: Callable[[], str],
        cipher: EnvelopeCipher,
        offload: SyncOffload,
        token_cache_factory: TokenCacheFactory = create_serializable_token_cache,
        application_factory: MsalApplicationFactory = create_confidential_client_application,
        now: Callable[[], datetime] | None = None,
        pending_ttl: timedelta = timedelta(minutes=10),
    ) -> None:
        self._fabric_tenant_id = fabric_tenant_id
        self._fabric_client_id = fabric_client_id
        self._client_assertion_callback = client_assertion_callback
        self._cipher = cipher
        self._offload = offload
        self._token_cache_factory = token_cache_factory
        self._application_factory = application_factory
        self._now = now or (lambda: datetime.now(UTC))
        self._pending_ttl = pending_ttl

    async def initiate_authorization_flow(self, *, provider: FabricProvider, redirect_uri: str) -> dict[str, Any]:
        cache = await self._offload(self._token_cache_factory)
        application = await self._offload(
            partial(
                self._application_factory,
                client_id=str(self._fabric_client_id),
                authority=fabric_authority(self._fabric_tenant_id),
                client_assertion_callback=self._client_assertion_callback,
                token_cache=cache,
            )
        )
        return await self._offload(
            partial(
                application.initiate_auth_code_flow,
                list(provider_application_scopes(provider)),
                redirect_uri=redirect_uri,
                response_mode="form_post",
            )
        )

    async def complete_authorization_flow(
        self,
        *,
        tenant_id: UUID,
        owner_object_id: UUID,
        provider: FabricProvider,
        flow: dict[str, Any],
        auth_response: dict[str, str],
    ) -> FabricPendingGrantRecord:
        cache = await self._offload(self._token_cache_factory)
        application = await self._offload(
            partial(
                self._application_factory,
                client_id=str(self._fabric_client_id),
                authority=fabric_authority(self._fabric_tenant_id),
                client_assertion_callback=self._client_assertion_callback,
                token_cache=cache,
            )
        )

        try:
            raw_result = await self._offload(application.acquire_token_by_auth_code_flow, flow, auth_response)
        except ValueError as exc:
            raise FabricAuthorizationRequired("Fabric authorization is required.") from exc
        result: dict[str, object] = cast(dict[str, object], raw_result)

        if "error" in result:
            raise FabricAuthorizationRequired("Fabric authorization is required.")

        claims = result.get("id_token_claims")
        if not isinstance(claims, dict):
            raise FabricAuthorizationRequired("Fabric authorization is required.")
        claim_values = cast(dict[str, object], claims)
        tid = claim_values.get("tid")
        if str(tid) != str(self._fabric_tenant_id):
            raise FabricAuthorizationRequired("Fabric authorization is required.")

        accounts = await self._offload(application.get_accounts)
        if len(accounts) != 1:
            raise FabricAuthorizationRequired("Fabric authorization is required.")
        home_account_id = accounts[0].get("home_account_id")
        if not isinstance(home_account_id, str) or not home_account_id:
            raise FabricAuthorizationRequired("Fabric authorization is required.")

        serialized_cache = await self._offload(cache.serialize)
        cache_bytes = serialized_cache.encode("utf-8")

        receipt = uuid4().hex
        record_id = FabricPendingGrantRecord.make_id(provider, receipt)
        context = EnvelopeContext(
            product_tenant_id=tenant_id,
            owner_object_id=owner_object_id,
            record_type="fabricPendingGrant",
            record_id=record_id,
        )
        encrypted_cache = await self._cipher.encrypt(cache_bytes, context)

        now = self._utc_now()
        return FabricPendingGrantRecord(
            id=record_id,
            tenant_id=tenant_id,
            owner_object_id=owner_object_id,
            provider=provider,
            receipt=receipt,
            scope_hash=provider_scope_hash(provider),
            audience_hash=audience_hash(),
            fabric_tenant_id=self._fabric_tenant_id,
            account_hash=_sha256_text(home_account_id),
            cache=encrypted_cache,
            expires_at=now + self._pending_ttl,
        )

    def _utc_now(self) -> datetime:
        value = self._now()
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class FabricMsalSilentTokenService:
    def __init__(
        self,
        *,
        repository: FabricGrantRepository,
        cipher: EnvelopeCipher,
        fabric_tenant_id: UUID,
        fabric_client_id: UUID,
        client_assertion_callback: Callable[[], str],
        offload: SyncOffload,
        token_cache_factory: TokenCacheFactory = create_serializable_token_cache,
        application_factory: MsalApplicationFactory = create_confidential_client_application,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._cipher = cipher
        self._fabric_tenant_id = fabric_tenant_id
        self._fabric_client_id = fabric_client_id
        self._client_assertion_callback = client_assertion_callback
        self._offload = offload
        self._token_cache_factory = token_cache_factory
        self._application_factory = application_factory
        self._now = now or (lambda: datetime.now(UTC))

    async def acquire_access_token(
        self,
        *,
        tenant_id: UUID,
        owner_object_id: UUID,
        provider: FabricProvider,
    ) -> AccessToken:
        for attempt in range(2):
            grant = await self._repository.get_grant(tenant_id, owner_object_id, provider)
            if grant is None:
                raise FabricAuthorizationRequired("Fabric authorization is required.")
            if grant.state is FabricGrantState.REAUTH_REQUIRED:
                raise FabricAuthorizationRequired("Fabric authorization is required.")

            try:
                return await self._acquire_and_persist(grant)
            except _ReauthorizationSignal:
                await self._persist_reauth_required(grant)
                raise FabricAuthorizationRequired("Fabric authorization is required.") from None
            except FabricGrantConflict:
                if attempt == 0:
                    continue
                raise

        raise FabricAuthorizationRequired("Fabric authorization is required.")

    async def _acquire_and_persist(self, grant: FabricGrantRecord) -> AccessToken:
        context = EnvelopeContext(
            product_tenant_id=grant.tenant_id,
            owner_object_id=grant.owner_object_id,
            record_type="fabricGrant",
            record_id=grant.id,
        )
        encrypted_cache = grant.cache
        decrypted_cache = await self._cipher.decrypt(encrypted_cache, context)

        cache = await self._offload(self._token_cache_factory)
        await self._offload(cache.deserialize, decrypted_cache.decode("utf-8"))
        serialized_before = await self._offload(cache.serialize)

        application = await self._offload(
            partial(
                self._application_factory,
                client_id=str(self._fabric_client_id),
                authority=fabric_authority(self._fabric_tenant_id),
                client_assertion_callback=self._client_assertion_callback,
                token_cache=cache,
            )
        )

        accounts = await self._offload(application.get_accounts)
        if len(accounts) != 1:
            raise _ReauthorizationSignal
        account = accounts[0]

        raw_result = await self._offload(
            application.acquire_token_silent,
            list(provider_application_scopes(grant.provider)),
            account,
        )
        if raw_result is None:
            raise _ReauthorizationSignal
        result: dict[str, object] = cast(dict[str, object], raw_result)
        if "error" in result:
            if result.get("error") in {"invalid_grant", "interaction_required"}:
                raise _ReauthorizationSignal
            raise _ReauthorizationSignal

        access_token = result.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise _ReauthorizationSignal

        if grant.scope_hash != provider_scope_hash(grant.provider) or grant.audience_hash != audience_hash():
            raise _ReauthorizationSignal

        if not _has_expected_access_token_binding(
            access_token,
            fabric_tenant_id=self._fabric_tenant_id,
            expected_scopes=provider_application_scopes(grant.provider),
            expected_audience_hash=grant.audience_hash,
            now=self._utc_now(),
        ):
            raise _ReauthorizationSignal

        serialized_after = await self._offload(cache.serialize)
        if serialized_after != serialized_before:
            replacement_cache = await self._cipher.encrypt(serialized_after.encode("utf-8"), context)
        else:
            replacement_cache = encrypted_cache

        now = self._utc_now()
        refreshed = grant.model_copy(
            update={
                "state": FabricGrantState.LINKED,
                "cache": replacement_cache,
                "last_used_at": now,
                "expires_at": now + timedelta(days=30),
            }
        )
        etag = grant.etag
        if not isinstance(etag, str):
            raise FabricGrantConflict("grant ETag missing")
        try:
            await self._repository.replace_grant(refreshed, etag=etag)
        except FabricGrantConflict:
            if serialized_after != serialized_before:
                raise
            current = await self._repository.get_grant(grant.tenant_id, grant.owner_object_id, grant.provider)
            metadata_fields = {"etag", "last_used_at", "expires_at"}
            if (
                current is None
                or current.expires_at <= self._utc_now()
                or current.model_dump(exclude=metadata_fields) != grant.model_dump(exclude=metadata_fields)
            ):
                raise

        expires_on = _resolve_expires_on(now=now, result=result)
        return AccessToken(token=access_token, expires_on=expires_on)

    async def _persist_reauth_required(self, grant: FabricGrantRecord) -> None:
        for attempt in range(2):
            current = (
                grant
                if attempt == 0
                else await self._repository.get_grant(grant.tenant_id, grant.owner_object_id, grant.provider)
            )
            if current is None:
                return
            if current.state is FabricGrantState.REAUTH_REQUIRED:
                return
            etag = current.etag
            if not isinstance(etag, str):
                return
            try:
                update = current.model_copy(update={"state": FabricGrantState.REAUTH_REQUIRED})
                await self._repository.replace_grant(update, etag=etag)
                return
            except FabricGrantConflict:
                if attempt == 0:
                    continue
                return

    def _utc_now(self) -> datetime:
        value = self._now()
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class _ReauthorizationSignal(Exception):
    pass


def _resolve_expires_on(*, now: datetime, result: Mapping[str, object]) -> int:
    expires_on = result.get("expires_on")
    if isinstance(expires_on, int):
        return expires_on
    if isinstance(expires_on, str) and expires_on.isdigit():
        return int(expires_on)

    expires_in = result.get("expires_in")
    if isinstance(expires_in, int):
        return int((now + timedelta(seconds=max(expires_in, 0))).timestamp())
    if isinstance(expires_in, str) and expires_in.isdigit():
        return int((now + timedelta(seconds=max(int(expires_in), 0))).timestamp())
    return int((now + timedelta(minutes=5)).timestamp())


def _has_expected_access_token_binding(
    access_token: str,
    *,
    fabric_tenant_id: UUID,
    expected_scopes: tuple[str, ...],
    expected_audience_hash: str,
    now: datetime,
) -> bool:
    claims = _jwt_claims(access_token)
    if claims is None or str(claims.get("tid")) != str(fabric_tenant_id):
        return False
    audience = claims.get("aud")
    if not isinstance(audience, str) or _sha256_text(audience) != expected_audience_hash:
        return False
    scope_value = claims.get("scp")
    if not isinstance(scope_value, str):
        return False
    granted = {value for value in scope_value.split(" ") if value}
    required = {scope.rsplit("/", 1)[-1] for scope in expected_scopes}
    if not required.issubset(granted):
        return False
    expires_at = claims.get("exp")
    if not isinstance(expires_at, int) or expires_at <= int(now.timestamp()):
        return False
    return True


def _jwt_claims(access_token: str) -> dict[str, object] | None:
    parts = access_token.split(".")
    if len(parts) != 3:
        return None
    try:
        payload = parts[1].encode("ascii")
        decoded = base64.urlsafe_b64decode(payload + b"=" * (-len(payload) % 4))
        raw = json.loads(decoded)
    except (UnicodeEncodeError, UnicodeDecodeError, binascii.Error, json.JSONDecodeError):
        return None
    if not isinstance(raw, Mapping):
        return None
    claims: dict[str, object] = {}
    for key, value in cast(Mapping[object, object], raw).items():
        if not isinstance(key, str):
            return None
        claims[key] = value
    return claims


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def redact_msal_result(result: Mapping[str, object]) -> dict[str, object]:
    """Small helper for tests and diagnostics with token fields stripped."""
    redacted = dict(result)
    for key in ("access_token", "refresh_token", "id_token"):
        redacted.pop(key, None)
    return redacted


def deserialize_token_cache(cache: TokenCacheProtocol, payload: bytes) -> None:
    cache.deserialize(payload.decode("utf-8"))


def serialize_token_cache(cache: TokenCacheProtocol) -> bytes:
    return cache.serialize().encode("utf-8")


def stable_flow_payload(flow: dict[str, Any]) -> bytes:
    return json.dumps(flow, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")
