from __future__ import annotations

from collections.abc import Callable

from redis_entraid.cred_provider import (
    EntraIdCredentialsProvider,
    create_from_managed_identity,  # pyright: ignore[reportUnknownVariableType]
)
from redis_entraid.identity_provider import ManagedIdentityIdType, ManagedIdentityType

RedisCredentialProviderFactory = Callable[..., EntraIdCredentialsProvider]


def create_redis_credential_provider(
    managed_identity_client_id: str | None,
    *,
    provider_factory: RedisCredentialProviderFactory = create_from_managed_identity,
) -> EntraIdCredentialsProvider:
    if managed_identity_client_id is None:
        return provider_factory(
            identity_type=ManagedIdentityType.SYSTEM_ASSIGNED,
            resource="https://redis.azure.com/",
        )
    return provider_factory(
        identity_type=ManagedIdentityType.USER_ASSIGNED,
        resource="https://redis.azure.com/",
        id_type=ManagedIdentityIdType.CLIENT_ID,
        id_value=managed_identity_client_id,
    )
