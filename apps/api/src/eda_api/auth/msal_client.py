from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, Protocol, cast
from uuid import UUID

from azure.identity import ManagedIdentityCredential
from msal import ConfidentialClientApplication

from eda_api.config import Settings

from .models import Principal


class MsalApplication(Protocol):
    def initiate_auth_code_flow(self, scopes: list[str], **kwargs: Any) -> dict[str, Any]: ...

    def acquire_token_by_auth_code_flow(self, flow: dict[str, Any], response: dict[str, str]) -> dict[str, Any]: ...


class AuthenticationError(ValueError):
    pass


class MsalAuthClient:
    def __init__(
        self,
        settings: Settings,
        application_factory: Callable[[], MsalApplication] | None = None,
    ) -> None:
        self.settings = settings
        self._managed_identity: ManagedIdentityCredential | None = None
        self._application_factory = application_factory or self._build_application

    def _client_credential(self) -> str | dict[str, Callable[[], str]]:
        if self.settings.managed_identity_client_id is not None:
            if self._managed_identity is None:
                self._managed_identity = ManagedIdentityCredential(
                    client_id=str(self.settings.managed_identity_client_id)
                )

            def assertion() -> str:
                assert self._managed_identity is not None
                return self._managed_identity.get_token("api://AzureADTokenExchange/.default").token

            return {"client_assertion": assertion}
        assert self.settings.entra_client_secret is not None
        return self.settings.entra_client_secret.get_secret_value()

    def _build_application(self) -> MsalApplication:
        return cast(
            MsalApplication,
            ConfidentialClientApplication(
                client_id=str(self.settings.entra_client_id),
                authority=self.settings.authority,
                client_credential=self._client_credential(),
                exclude_scopes=["offline_access"],
            ),
        )

    async def initiate(self) -> dict[str, Any]:
        application = self._application_factory()
        return await asyncio.to_thread(
            application.initiate_auth_code_flow,
            [],
            redirect_uri=self.settings.redirect_uri,
            response_mode="form_post",
        )

    async def complete(self, flow: dict[str, Any], response: dict[str, str]) -> Principal:
        application = self._application_factory()
        try:
            result = await asyncio.to_thread(application.acquire_token_by_auth_code_flow, flow, response)
        except ValueError as error:
            raise AuthenticationError("authorization response validation failed") from error
        if "error" in result:
            raise AuthenticationError(str(result.get("error")))
        raw_claims = result.get("id_token_claims")
        if not isinstance(raw_claims, dict):
            raise AuthenticationError("validated ID token claims are missing")
        claims = cast(dict[str, object], raw_claims)
        try:
            principal = Principal(
                tenant_id=UUID(str(claims["tid"])),
                owner_object_id=UUID(str(claims["oid"])),
                audience=UUID(str(claims["aud"])),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise AuthenticationError("required immutable claims are invalid") from error
        if principal.tenant_id != self.settings.entra_tenant_id:
            raise AuthenticationError("tenant claim mismatch")
        if principal.audience != self.settings.entra_client_id:
            raise AuthenticationError("audience claim mismatch")
        return principal

    def close(self) -> None:
        if self._managed_identity is not None:
            self._managed_identity.close()
