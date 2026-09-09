from __future__ import annotations

from typing import Any

from agent_framework.foundry import FoundryChatClient
from azure.identity.aio import AzureCliCredential, ManagedIdentityCredential


def create_foundry_client(
    settings: Any,
    tokenizer: Any,
) -> tuple[FoundryChatClient, AzureCliCredential | ManagedIdentityCredential]:
    local = settings.app_env != "production"
    if local:
        credential = AzureCliCredential()
    else:
        client_id = settings.managed_identity_client_id
        credential = (
            ManagedIdentityCredential()
            if client_id is None
            else ManagedIdentityCredential(client_id=str(client_id))
        )
    client = FoundryChatClient(
        project_endpoint=str(settings.foundry_project_endpoint),
        model=settings.foundry_model_deployment,
        credential=credential,
        tokenizer=tokenizer,
    )
    return client, credential
