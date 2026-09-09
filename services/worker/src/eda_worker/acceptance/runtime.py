from __future__ import annotations

import base64
import os
from contextlib import AsyncExitStack
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from azure.cosmos.aio import CosmosClient
from azure.cosmos.exceptions import CosmosHttpResponseError
from azure.identity import ManagedIdentityCredential as SyncManagedIdentityCredential
from azure.identity.aio import ManagedIdentityCredential
from azure.storage.blob.aio import BlobServiceClient
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from eda_fabric_auth import CosmosFabricGrantRepository, EnvelopeCipher, FabricProvider, KeyVaultKeyWrapper
from eda_runtime_state.tasks import CosmosRuntimeStateRepository
from pydantic import AnyHttpUrl, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from eda_worker.acceptance.fabric import (
    FabricAcceptanceInput,
    FabricAcceptanceResult,
    acceptance_input_aad,
    run_fabric_acceptance,
    sanitized_result_document,
)
from eda_worker.fabric.config import FabricSettings
from eda_worker.fabric.contracts import FabricPrincipal
from eda_worker.fabric.gateway import ArtifactFabricResultStore, FabricIQGateway
from eda_worker.fabric.mcp_client import FabricMcpClient
from eda_worker.fabric.planner import FabricAnalystPlanner, FabricPlanningClient
from eda_worker.fabric.provenance import CosmosFabricQueryRepository, FabricQueryRecorder
from eda_worker.fabric.readiness import (
    FabricFeatureRecord,
    FabricModelIdentity,
    FabricReadinessStatus,
    load_fabric_runtime_state,
)
from eda_worker.fabric.token_provider import FabricAccessTokenProvider
from eda_worker.model.client import create_foundry_client
from eda_worker.model.profiles import WorkClass
from eda_worker.model.startup import load_startup_model_state
from eda_worker.sandbox.gateway import CosmosBlobArtifactGatewayStore


class FabricAcceptanceSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EDA_", extra="ignore", populate_by_name=True)

    app_env: str = "production"
    managed_identity_client_id: UUID
    cosmos_endpoint: AnyHttpUrl
    cosmos_database: str = "enterprise-data-analyst"
    cosmos_workspace_container: str = "workspace"
    cosmos_runtime_container: str = "runtime"
    cosmos_auth_container: str = "auth"
    cosmos_fabric_auth_container: str = "fabricAuth"
    blob_account_url: AnyHttpUrl
    blob_sessions_container: str = "sessions"
    foundry_project_endpoint: AnyHttpUrl
    foundry_model_deployment: str
    eda_model_profile: str = "gpt-5.6-terra-medium-v1"
    foundry_hosting: str = "azure"
    deployment_id: str = Field(min_length=1, max_length=128)
    model_contract_path: Path = Path("/app/config/model-contract.json")
    tokenizer_calibration_path: Path = Path("/app/config/tokenizer-calibration.json")


async def run_semantic_acceptance_job(
    run_id: str,
    *,
    settings: FabricAcceptanceSettings | None = None,
    fabric_settings: FabricSettings | None = None,
) -> FabricAcceptanceResult:
    config = settings or FabricAcceptanceSettings.model_validate({})
    fabric = fabric_settings or FabricSettings()
    if os.getenv("FABRIC_ACCEPTANCE_MODE") != "true":
        raise ValueError("Fabric acceptance mode is disabled")
    if (
        not fabric.enabled
        or fabric.provider is not FabricProvider.SEMANTIC_MODEL
        or fabric.tenant_id is None
        or fabric.client_id is None
        or fabric.key_vault_url is None
        or fabric.signing_certificate_name is None
        or fabric.cache_wrap_key_name is None
    ):
        raise ValueError("semantic Fabric acceptance configuration is incomplete")

    startup = load_startup_model_state(config)
    resources = AsyncExitStack()
    try:
        client_id = str(config.managed_identity_client_id)
        credential = ManagedIdentityCredential(client_id=client_id)
        resources.push_async_callback(credential.close)
        sync_credential = SyncManagedIdentityCredential(client_id=client_id)
        resources.callback(sync_credential.close)

        cosmos = CosmosClient(str(config.cosmos_endpoint), credential=credential)
        resources.push_async_callback(cosmos.close)
        database = cosmos.get_database_client(config.cosmos_database)
        workspace = database.get_container_client(config.cosmos_workspace_container)
        runtime = database.get_container_client(config.cosmos_runtime_container)
        runtime_repository = CosmosRuntimeStateRepository(workspace, runtime)

        blobs = BlobServiceClient(account_url=str(config.blob_account_url), credential=credential)
        resources.push_async_callback(blobs.close)
        artifact_store = CosmosBlobArtifactGatewayStore(
            runtime_repository,
            workspace,
            blobs.get_container_client(config.blob_sessions_container),
        )

        foundry, foundry_credential = create_foundry_client(config, startup.tokenizer)
        resources.push_async_callback(foundry.project_client.close)
        resources.push_async_callback(foundry_credential.close)

        active_model = FabricModelIdentity(
            modelProfile=startup.contract.model_profile.value,
            modelDeployment=startup.contract.deployment,
            servedModel=startup.contract.base_model,
            servedSnapshot=startup.contract.base_model_snapshot,
            promptVersion=startup.contract.prompt_version,
            promptSha256=startup.contract.prompt_sha256,
            requestOptionsSha256=startup.contract.request_options_sha256,
        )
        loaded = await load_fabric_runtime_state(
            cast(Any, runtime),
            enabled=True,
            fabric_tenant_id=fabric.tenant_id,
            active_model=active_model,
            deployment_id=config.deployment_id,
            now=datetime.now(UTC),
        )
        if loaded.readiness.status is not FabricReadinessStatus.CONFIGURED or loaded.contract is None:
            raise ValueError("Fabric acceptance requires configured provider state")

        feature_raw = cast(
            dict[str, Any],
            await runtime.read_item(item="feature:fabric", partition_key="feature:fabric"),
        )
        feature = FabricFeatureRecord.model_validate(
            {key: value for key, value in feature_raw.items() if not key.startswith("_")}
        )
        input_id = f"fabric-acceptance-input:{run_id}"
        input_document = cast(dict[str, Any], await runtime.read_item(item=input_id, partition_key=input_id))

        key_vault_url = str(fabric.key_vault_url).rstrip("/")
        key_wrapper = KeyVaultKeyWrapper(f"{key_vault_url}/keys/{fabric.cache_wrap_key_name}", credential)
        resources.push_async_callback(key_wrapper.close)
        acceptance_input = await _decrypt_input(input_document, key_wrapper)
        if acceptance_input.run_id != run_id:
            raise ValueError("Fabric acceptance input run ID does not match")

        cipher = EnvelopeCipher(key_wrapper)
        grants = CosmosFabricGrantRepository(
            cast(Any, database.get_container_client(config.cosmos_auth_container)),
            cast(Any, database.get_container_client(config.cosmos_fabric_auth_container)),
        )
        token_provider = FabricAccessTokenProvider(
            repository=grants,
            cipher=cipher,
            fabric_tenant_id=fabric.tenant_id,
            fabric_client_id=fabric.client_id,
            key_vault_url=key_vault_url,
            signing_certificate_name=fabric.signing_certificate_name,
            credential=sync_credential,
        )
        resources.callback(token_provider.close)
        expected_tools = tuple(
            {
                "name": tool.name,
                "description": tool.description,
                "inputSchema": tool.input_schema,
            }
            for tool in loaded.contract.tools
        )

        def planner_factory(principal: FabricPrincipal) -> FabricAnalystPlanner:
            async def owner_token() -> str:
                token = await token_provider.acquire_for_principal(
                    principal=principal,
                    provider=FabricProvider.SEMANTIC_MODEL,
                )
                return token.token

            return FabricAnalystPlanner(
                cast(FabricPlanningClient, foundry),
                FabricMcpClient(token_provider=owner_token, expected_tools=expected_tools),
                model_options=startup.contract.options_for(WorkClass.ANALYSIS),
                max_turns=fabric.max_analyst_turns,
            )

        gateway = FabricIQGateway(
            planner_factory=planner_factory,
            runtime=runtime_repository,
            result_store=ArtifactFabricResultStore(artifact_store),
            models=fabric.models,
            evidence_recorder=FabricQueryRecorder(
                artifact_store=artifact_store,
                repository=CosmosFabricQueryRepository(workspace),
            ),
        )
        result = await run_fabric_acceptance(
            acceptance_input,
            feature=feature,
            gateway=gateway,
            acceptance_mode=True,
        )
        await _write_result(runtime, result)
        return result
    finally:
        await resources.aclose()


async def _decrypt_input(document: dict[str, Any], key_wrapper: KeyVaultKeyWrapper) -> FabricAcceptanceInput:
    required = {
        "id",
        "recordType",
        "schemaVersion",
        "runId",
        "deploymentId",
        "providerContractSha256",
        "keyId",
        "algorithm",
        "wrappedDek",
        "nonce",
        "ciphertext",
        "expiresAt",
        "ttl",
    }
    clean = {key: value for key, value in document.items() if not key.startswith("_")}
    if set(clean) != required or clean["recordType"] != "fabricAcceptanceInput":
        raise ValueError("encrypted Fabric acceptance input is malformed")
    if clean["algorithm"] != "RSA-OAEP-256" or clean["keyId"] != key_wrapper.key_id:
        raise ValueError("encrypted Fabric acceptance input uses an unexpected key")
    expires_at = datetime.fromisoformat(cast(str, clean["expiresAt"]))
    if expires_at <= datetime.now(UTC):
        raise ValueError("encrypted Fabric acceptance input expired")
    wrapped = _decode(cast(str, clean["wrappedDek"]))
    data_key = await key_wrapper.unwrap_key(wrapped, cast(str, clean["keyId"]))
    aad = acceptance_input_aad(
        record_id=cast(str, clean["id"]),
        deployment_id=cast(str, clean["deploymentId"]),
        provider_contract_sha256=cast(str, clean["providerContractSha256"]),
    )
    plaintext = AESGCM(data_key).decrypt(
        _decode(cast(str, clean["nonce"])),
        _decode(cast(str, clean["ciphertext"])),
        aad,
    )
    return FabricAcceptanceInput.model_validate_json(plaintext)


async def _write_result(runtime: Any, result: FabricAcceptanceResult) -> None:
    document = dict(sanitized_result_document(result))
    try:
        await runtime.create_item(document)
    except CosmosHttpResponseError as error:
        if error.status_code != 409:
            raise
        existing = cast(
            dict[str, Any],
            await runtime.read_item(item=document["id"], partition_key=document["id"]),
        )
        comparable = {key: value for key, value in existing.items() if not key.startswith("_")}
        if comparable != document:
            raise ValueError("Fabric acceptance result conflict") from error


def _decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)
