from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import partial
from typing import Any, Protocol, cast
from uuid import UUID

import anyio
from azure.core.credentials import AccessToken, TokenCredential
from azure.keyvault.certificates import CertificateClient
from azure.keyvault.keys.crypto import CryptographyClient
from eda_fabric_auth import EnvelopeCipher, FabricGrantRepository, FabricProvider
from eda_fabric_auth.assertion import FabricClientAssertionFactory, SigningClient, SigningClientFactory
from eda_fabric_auth.msal_cache import FabricMsalSilentTokenService

from .contracts import FabricPrincipal


class RunSync(Protocol):
    async def __call__[T](self, func: Callable[[], T], *, limiter: anyio.CapacityLimiter) -> T: ...


class _CertificateReader:
    def __init__(self, client: CertificateClient) -> None:
        self._client = client

    def get_certificate(self, certificate_name: str, /) -> Any:
        return self._client.get_certificate(certificate_name)


class _KeyVaultSigningClient(SigningClient):
    def __init__(self, key_id: str, credential: TokenCredential) -> None:
        self._client = CryptographyClient(key_id, credential)

    def sign(self, algorithm: Any, digest: bytes) -> Any:
        return self._client.sign(algorithm, digest)

    def close(self) -> None:
        self._client.close()


class _KeyVaultSignerFactory(SigningClientFactory):
    def __init__(self, credential: TokenCredential) -> None:
        self._credential = credential
        self._clients: dict[str, _KeyVaultSigningClient] = {}

    def __call__(self, key_id: str, /) -> SigningClient:
        client = self._clients.get(key_id)
        if client is None:
            client = _KeyVaultSigningClient(key_id, self._credential)
            self._clients[key_id] = client
        return client

    def close(self) -> None:
        for client in self._clients.values():
            client.close()
        self._clients.clear()


class FabricAccessTokenProvider:
    def __init__(
        self,
        *,
        repository: FabricGrantRepository,
        cipher: EnvelopeCipher,
        fabric_tenant_id: UUID,
        fabric_client_id: UUID,
        key_vault_url: str,
        signing_certificate_name: str,
        credential: TokenCredential,
        limiter: anyio.CapacityLimiter | None = None,
        run_sync: RunSync | None = None,
    ) -> None:
        self._limiter = limiter or anyio.CapacityLimiter(8)
        self._run_sync: RunSync = run_sync or _default_run_sync

        certificate_reader = _CertificateReader(CertificateClient(vault_url=key_vault_url, credential=credential))
        signer_factory = _KeyVaultSignerFactory(credential)
        self._signer_factory = signer_factory

        assertion_factory = FabricClientAssertionFactory(
            fabric_tenant_id=fabric_tenant_id,
            fabric_client_id=fabric_client_id,
            certificate_name=signing_certificate_name,
            certificate_reader=certificate_reader,
            signing_client_factory=signer_factory,
        )

        self._silent_service = FabricMsalSilentTokenService(
            repository=repository,
            cipher=cipher,
            fabric_tenant_id=fabric_tenant_id,
            fabric_client_id=fabric_client_id,
            client_assertion_callback=assertion_factory.callback(),
            offload=self._offload,
        )

    async def acquire_for_principal(self, *, principal: FabricPrincipal, provider: FabricProvider) -> AccessToken:
        return await self._silent_service.acquire_access_token(
            tenant_id=principal.tenant_id,
            owner_object_id=principal.owner_object_id,
            provider=provider,
        )

    async def _offload(self, func: Callable[..., Any], /, *args: Any) -> Any:
        return await self._run_sync(partial(func, *args), limiter=self._limiter)

    def close(self) -> None:
        self._signer_factory.close()


async def _default_run_sync[T](func: Callable[[], T], *, limiter: anyio.CapacityLimiter) -> T:
    runner = cast(
        Callable[..., Awaitable[object]],
        anyio.to_thread.run_sync,  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue]
    )
    result = await runner(func, limiter=limiter)
    return cast(T, result)
