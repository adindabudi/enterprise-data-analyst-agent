from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Protocol, cast

from azure.cosmos.exceptions import CosmosResourceNotFoundError
from eda_contracts import ApiProblem
from eda_fabric_auth import CosmosFabricGrantRepository, EnvelopeCipher, FabricProvider, KeyVaultKeyWrapper
from eda_runtime_state.events import RedisEventStore
from eda_runtime_state.messages import CosmosMessageRepository, InMemoryMessageRepository, MessageRepository
from eda_runtime_state.redis_auth import create_redis_credential_provider
from eda_runtime_state.tasks import CosmosRuntimeStateRepository, RuntimeStateRepository
from fastapi import APIRouter, FastAPI, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field

from eda_api.auth.msal_client import MsalAuthClient
from eda_api.auth.repository import AuthRepository, CosmosAuthRepository
from eda_api.auth.routes import router as auth_router
from eda_api.chat.model import load_interactive_model_config
from eda_api.chat.service import InteractiveChatService, MafInteractiveChatService
from eda_api.chat.sessions import CosmosInteractiveSessionStore, RedisInteractiveSessionStore
from eda_api.config import DEFAULT_FRONTEND_DIST, Settings
from eda_api.fabric_auth import FabricAuthCoordinator, FabricAuthorizationCodeClient
from eda_api.fabric_auth.graph import FabricGraphQueryService
from eda_api.fabric_auth.msal_client import FabricAccessTokenClient
from eda_api.fabric_auth.ontology import FabricOntologyQueryService
from eda_api.fabric_auth.routes import router as fabric_auth_router
from eda_api.hosted_responses import HostedResponsesClient
from eda_api.problems import install_problem_handlers
from eda_api.readiness.models import (
    DocumentFeatureState,
    FabricFeatureState,
    FabricPackStatus,
    PowerBiProjectStatus,
    document_pack_status,
    fabric_pack_status,
)
from eda_api.routes.chat import router as chat_router
from eda_api.routes.session_tasks import router as session_tasks_router
from eda_api.routes.sessions import router as sessions_router
from eda_api.routes.tasks import router as tasks_router
from eda_api.routes.uploads import router as uploads_router
from eda_api.storage.artifacts import ArtifactCatalog, CosmosBlobArtifactCatalog
from eda_api.storage.history import CosmosSessionHistoryReader, SessionHistoryReader
from eda_api.storage.inputs import CosmosBlobInputArtifactWriter, InputArtifactWriter
from eda_api.storage.query_results import CosmosBlobQueryResultWriter, QueryResultWriter
from eda_api.storage.todos import CosmosSessionTodoReader, SessionTodoReader
from eda_api.storage.uploads import AzureBlobStore, CosmosUploadRepository, UploadService
from eda_api.storage.workspace import CosmosWorkspaceRepository, WorkspaceRepository
from eda_api.task_service import HostedTaskClient, TaskEventStore, TaskService
from eda_api.telemetry import configure_logging, configure_telemetry

# Matches the worker's hard ceiling so both paths bound a conversation the same way.
INTERACTIVE_CONTEXT_WINDOW_TOKENS = 128_000
INTERACTIVE_MAX_OUTPUT_TOKENS = 8_000
INTERACTIVE_MAX_TOOL_ITERATIONS = 12
INTERACTIVE_MAX_TOOL_CALLS = 24


class ClosableMsalClient(Protocol):
    async def initiate(self) -> dict[str, object]: ...

    async def complete(self, flow: dict[str, object], response: dict[str, str]) -> object: ...

    def close(self) -> None: ...


class HealthStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: str


class ReadinessStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    status: str
    components: dict[str, str]
    feature_packs: dict[str, str] = Field(serialization_alias="featurePacks")


def _frontend_dist(settings_override: Settings | None) -> Path:
    if settings_override is not None:
        return settings_override.frontend_dist
    configured = os.environ.get("EDA_FRONTEND_DIST")
    return Path(configured) if configured else DEFAULT_FRONTEND_DIST


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    msal: ClosableMsalClient | None = None
    credential = None
    cosmos_client = None
    database = None
    blob_service = None
    redis_client = None
    hosted_client = None
    fabric_key_wrapper = None
    fabric_auth_client = None
    fabric_access_token_client = None
    fabric_sync_credential = None
    fabric_query = None
    graph_query = None
    interactive_client = None
    interactive_credential = None
    interactive_agent = None
    interactive_model = None
    interactive_web_search_tool = None
    try:
        config = app.state.settings_override or Settings.model_validate({})
        repository = app.state.auth_repository_override
        workspace = app.state.workspace_repository_override
        uploads = app.state.upload_service_override
        artifact_catalog = app.state.artifact_catalog_override
        session_todos = app.state.session_todo_reader_override
        session_history = app.state.session_history_reader_override
        query_result_writer = app.state.query_result_writer_override
        input_artifact_writer = app.state.input_artifact_writer_override
        runtime_repository = app.state.runtime_repository_override
        event_store = app.state.event_store_override
        message_repository = app.state.message_repository_override
        hosted_client = app.state.hosted_client_override
        fabric_auth_service = app.state.fabric_auth_service_override
        used_managed_resources = False
        if (
            repository is None
            or workspace is None
            or uploads is None
            or runtime_repository is None
            or event_store is None
        ):
            used_managed_resources = True
            from azure.cosmos.aio import CosmosClient
            from azure.identity.aio import DefaultAzureCredential
            from azure.storage.blob.aio import BlobServiceClient
            from redis.asyncio import Redis

            if config.managed_identity_client_id is None:
                credential = DefaultAzureCredential()
            else:
                credential = DefaultAzureCredential(managed_identity_client_id=str(config.managed_identity_client_id))
            cosmos_client = CosmosClient(str(config.cosmos_endpoint), credential=credential)
            database = cosmos_client.get_database_client(config.cosmos_database)
            if repository is None:
                repository = CosmosAuthRepository(database.get_container_client(config.cosmos_auth_container))
            if workspace is None:
                workspace = CosmosWorkspaceRepository(database.get_container_client(config.cosmos_workspace_container))
            if runtime_repository is None:
                runtime_repository = CosmosRuntimeStateRepository(
                    database.get_container_client(config.cosmos_workspace_container),
                    database.get_container_client(config.cosmos_runtime_container),
                )
            if message_repository is None:
                message_repository = CosmosMessageRepository(
                    database.get_container_client(config.cosmos_workspace_container)
                )
            if uploads is None:
                blob_service = BlobServiceClient(account_url=str(config.blob_account_url), credential=credential)
                uploads = UploadService(
                    blob_store=AzureBlobStore(blob_service.get_container_client(config.blob_quarantine_container)),
                    upload_repository=CosmosUploadRepository(
                        database.get_container_client(config.cosmos_workspace_container)
                    ),
                    upload_limit_bytes=config.upload_limit_bytes,
                )
            if artifact_catalog is None:
                artifact_catalog = CosmosBlobArtifactCatalog(
                    database.get_container_client(config.cosmos_workspace_container),
                    BlobServiceClient(
                        account_url=str(config.blob_account_url), credential=credential
                    ).get_container_client(config.blob_sessions_container),
                )
            if session_todos is None:
                session_todos = CosmosSessionTodoReader(
                    database.get_container_client(config.cosmos_workspace_container)
                )
            if session_history is None:
                session_history = CosmosSessionHistoryReader(
                    database.get_container_client(config.cosmos_workspace_container)
                )
            if query_result_writer is None:
                query_result_writer = CosmosBlobQueryResultWriter(
                    database.get_container_client(config.cosmos_workspace_container),
                    BlobServiceClient(
                        account_url=str(config.blob_account_url), credential=credential
                    ).get_container_client(config.blob_sessions_container),
                )
            if input_artifact_writer is None:
                input_artifact_writer = CosmosBlobInputArtifactWriter(
                    database.get_container_client(config.cosmos_workspace_container),
                    BlobServiceClient(
                        account_url=str(config.blob_account_url), credential=credential
                    ).get_container_client(config.blob_sessions_container),
                )
            if event_store is None:
                if config.app_env == "production":
                    redis_client = Redis.from_url(  # type: ignore[reportUnknownMemberType]
                        config.redis_url,
                        decode_responses=True,
                        credential_provider=create_redis_credential_provider(str(config.managed_identity_client_id)),
                    )
                else:
                    redis_client = Redis.from_url(config.redis_url, decode_responses=True)  # type: ignore[reportUnknownMemberType]
                event_store = RedisEventStore(
                    redis_client,
                    ttl_seconds=config.redis_stream_ttl_seconds,
                    max_entries=config.redis_stream_max_entries,
                )
        if message_repository is None:
            if config.app_env == "production":
                raise ValueError("message repository is required in production")
            if used_managed_resources:
                raise ValueError("message repository is unavailable")
            message_repository = InMemoryMessageRepository()
        if hosted_client is None:
            if credential is None:
                from azure.identity.aio import DefaultAzureCredential

                if config.managed_identity_client_id is None:
                    credential = DefaultAzureCredential()
                else:
                    credential = DefaultAzureCredential(
                        managed_identity_client_id=str(config.managed_identity_client_id)
                    )
            hosted_client = HostedResponsesClient(config.hosted_responses_endpoint, credential)
        msal = app.state.msal_override or MsalAuthClient(config)
        app.state.settings = config
        app.state.msal_client = msal
        app.state.auth_repository = repository
        app.state.workspace_repository = workspace
        app.state.upload_service = uploads
        app.state.artifact_catalog = artifact_catalog
        app.state.session_todo_reader = session_todos
        app.state.session_history_reader = session_history
        app.state.runtime_repository = runtime_repository
        app.state.event_store = event_store
        app.state.message_repository = message_repository
        app.state.hosted_client = hosted_client
        app.state.task_service = TaskService(
            cast(RuntimeStateRepository, runtime_repository),
            cast(MessageRepository, message_repository),
            cast(HostedTaskClient, hosted_client),
            cast(TaskEventStore | None, event_store),
            cast(QueryResultWriter | None, query_result_writer),
            uploads=cast(UploadService, uploads),
            input_artifact_writer=cast(InputArtifactWriter | None, input_artifact_writer),
        )
        interactive_chat_service = app.state.interactive_chat_service_override
        if interactive_chat_service is None and config.core_ready:
            from agent_framework import ContextWindowCompactionStrategy
            from agent_framework.foundry import FoundryChatClient
            from azure.identity.aio import ManagedIdentityCredential

            if config.managed_identity_client_id is None:
                raise ValueError("interactive chat requires the API managed identity")
            interactive_model = load_interactive_model_config(
                contract_path=Path(config.model_contract_path),
                prompt_path=Path(config.interactive_prompt_path),
                expected_deployment=config.foundry_model_deployment,
            )
            interactive_credential = ManagedIdentityCredential(client_id=str(config.managed_identity_client_id))
            interactive_client = FoundryChatClient(
                project_endpoint=str(config.foundry_project_endpoint),
                model=interactive_model.deployment,
                credential=interactive_credential,
            )
            # One Fabric call costs about twenty seconds, so the framework default of 40 round
            # trips would let a confused turn run for minutes before anyone could see why.
            interactive_client.function_invocation_configuration["max_iterations"] = INTERACTIVE_MAX_TOOL_ITERATIONS
            interactive_client.function_invocation_configuration["max_function_calls"] = INTERACTIVE_MAX_TOOL_CALLS
            interactive_agent = interactive_client.as_agent(
                name="enterprise-data-analyst-interactive",
                instructions=interactive_model.instructions,
                tools=(),
                default_options=interactive_model.options,
                compaction_strategy=ContextWindowCompactionStrategy(
                    max_context_window_tokens=INTERACTIVE_CONTEXT_WINDOW_TOKENS,
                    max_output_tokens=INTERACTIVE_MAX_OUTPUT_TOKENS,
                ),
            )
            interactive_web_search_tool = FoundryChatClient.get_web_search_tool()
        fabric_readiness = app.state.fabric_readiness_override
        if fabric_readiness is None:
            fabric_feature: FabricFeatureState | None = None
            if config.fabric_enabled and database is not None:
                try:
                    feature_id = (
                        "feature:fabric-ontology"
                        if config.fabric_provider is not None and config.fabric_provider.value == "ontology"
                        else "feature:fabric"
                    )
                    feature_document = await database.get_container_client(config.cosmos_runtime_container).read_item(
                        item=feature_id,
                        partition_key=feature_id,
                    )
                    fabric_feature = FabricFeatureState.model_validate(
                        {key: value for key, value in feature_document.items() if not key.startswith("_")}
                    )
                except (CosmosResourceNotFoundError, ValueError):
                    fabric_feature = None
            fabric_readiness = fabric_pack_status(enabled=config.fabric_enabled, feature=fabric_feature)
        app.state.fabric_readiness = fabric_readiness
        document_readiness = app.state.document_readiness_override
        if document_readiness is None:
            document_feature: DocumentFeatureState | None = None
            if config.documents_enabled and database is not None:
                try:
                    document = await database.get_container_client(config.cosmos_runtime_container).read_item(
                        item="feature:documents",
                        partition_key="feature:documents",
                    )
                    document_feature = DocumentFeatureState.model_validate(
                        {key: value for key, value in document.items() if not key.startswith("_")}
                    )
                except (CosmosResourceNotFoundError, ValueError):
                    document_feature = None
            document_readiness = document_pack_status(
                enabled=config.documents_enabled,
                feature=document_feature,
                deployment_id=config.deployment_id,
                worker_image_digest=config.worker_image_digest,
                sandbox_image_digest=config.sandbox_image_digest,
            )
        app.state.document_readiness = document_readiness
        app.state.powerbi_readiness = PowerBiProjectStatus.DISABLED
        if config.fabric_enabled:
            if fabric_auth_service is None:
                if credential is None or cosmos_client is None or database is None:
                    raise ValueError("fabric auth is enabled but managed API resources are unavailable")
                if (
                    config.fabric_provider is None
                    or config.fabric_tenant_id is None
                    or config.fabric_client_id is None
                    or config.fabric_key_vault_url is None
                    or config.fabric_signing_certificate_name is None
                    or config.fabric_cache_wrap_key_name is None
                ):
                    raise ValueError("enabled Fabric API configuration is incomplete")
                from azure.identity import DefaultAzureCredential as SyncDefaultAzureCredential

                if config.managed_identity_client_id is None:
                    fabric_sync_credential = SyncDefaultAzureCredential()
                else:
                    fabric_sync_credential = SyncDefaultAzureCredential(
                        managed_identity_client_id=str(config.managed_identity_client_id)
                    )
                fabric_key_id = (
                    f"{str(config.fabric_key_vault_url).rstrip('/')}/keys/{config.fabric_cache_wrap_key_name}"
                )
                fabric_key_wrapper = KeyVaultKeyWrapper(fabric_key_id, credential)
                cipher = EnvelopeCipher(fabric_key_wrapper)
                database_client = cast(Any, database)
                fabric_repository = CosmosFabricGrantRepository(
                    database_client.get_container_client(config.cosmos_auth_container),
                    database_client.get_container_client(config.cosmos_fabric_auth_container),
                )
                fabric_auth_client = FabricAuthorizationCodeClient(
                    fabric_tenant_id=config.fabric_tenant_id,
                    fabric_client_id=config.fabric_client_id,
                    key_vault_url=str(config.fabric_key_vault_url),
                    signing_certificate_name=config.fabric_signing_certificate_name,
                    cipher=cipher,
                    credential=fabric_sync_credential,
                )
                fabric_auth_service = FabricAuthCoordinator(
                    settings=config,
                    provider=config.fabric_provider,
                    client=fabric_auth_client,
                    repository=fabric_repository,
                    cipher=cipher,
                    task_service=app.state.task_service,
                )
                if (
                    config.fabric_provider is FabricProvider.ONTOLOGY
                    and config.fabric_tenant_id == config.entra_tenant_id
                    and len(config.fabric_ontologies) == 1
                    and fabric_readiness in {FabricPackStatus.CONFIGURED, FabricPackStatus.READY}
                ):
                    fabric_access_token_client = FabricAccessTokenClient(
                        repository=fabric_repository,
                        cipher=cipher,
                        fabric_tenant_id=config.fabric_tenant_id,
                        fabric_client_id=config.fabric_client_id,
                        key_vault_url=str(config.fabric_key_vault_url),
                        signing_certificate_name=config.fabric_signing_certificate_name,
                        credential=fabric_sync_credential,
                    )
                    fabric_query = FabricOntologyQueryService(
                        config.fabric_ontologies,
                        token_provider=fabric_access_token_client,
                    )
                    graph_query = FabricGraphQueryService(
                        next(iter(config.fabric_ontologies.values())),
                        token_provider=fabric_access_token_client,
                    )
            app.state.fabric_auth_service = fabric_auth_service
        else:
            app.state.fabric_auth_service = None
        # Chat can query a configured same-tenant source long before acceptance promotes it to ready.
        app.state.fabric_chat_query = fabric_query is not None
        if interactive_chat_service is None and config.core_ready:
            if interactive_agent is None or interactive_model is None:
                raise ValueError("interactive chat model is unavailable")
            interactive_chat_service = MafInteractiveChatService(
                agent=interactive_agent,
                messages=cast(MessageRepository, message_repository),
                options=interactive_model.options,
                analysis_starter=app.state.task_service,
                fabric_query=fabric_query,
                graph_query=graph_query,
                web_search_tool=interactive_web_search_tool,
                session_store=(
                    CosmosInteractiveSessionStore(
                        database.get_container_client(config.cosmos_workspace_container),
                        fallback=(
                            RedisInteractiveSessionStore(redis_client, ttl_seconds=config.redis_stream_ttl_seconds)
                            if redis_client is not None else None
                        ),
                    )
                    if database is not None
                    else None
                ),
            )
        app.state.interactive_chat_service = interactive_chat_service
        app.state.started = True
        yield
    finally:
        if interactive_client is not None:
            await interactive_client.project_client.close()
        if interactive_credential is not None:
            await interactive_credential.close()
        if fabric_auth_client is not None:
            fabric_auth_client.close()
        if fabric_access_token_client is not None:
            fabric_access_token_client.close()
        if fabric_sync_credential is not None:
            fabric_sync_credential.close()
        if fabric_key_wrapper is not None:
            await fabric_key_wrapper.close()
        if hosted_client is not None:
            await hosted_client.close()
        if msal is not None:
            msal.close()
        if blob_service is not None:
            await blob_service.close()
        if redis_client is not None:
            await redis_client.aclose()
        if cosmos_client is not None:
            await cosmos_client.close()
        if credential is not None:
            await credential.close()
        app.state.started = False


def create_app(
    settings_override: Settings | None = None,
    auth_repository_override: AuthRepository | None = None,
    msal_override: ClosableMsalClient | None = None,
    workspace_repository_override: WorkspaceRepository | None = None,
    upload_service_override: UploadService | None = None,
    runtime_repository_override: RuntimeStateRepository | None = None,
    event_store_override: TaskEventStore | None = None,
    message_repository_override: MessageRepository | None = None,
    hosted_client_override: HostedTaskClient | None = None,
    fabric_auth_service_override: FabricAuthCoordinator | None = None,
    fabric_readiness_override: FabricPackStatus | None = None,
    document_readiness_override: FabricPackStatus | None = None,
    interactive_chat_service_override: InteractiveChatService | None = None,
    artifact_catalog_override: ArtifactCatalog | None = None,
    session_todo_reader_override: SessionTodoReader | None = None,
    query_result_writer_override: QueryResultWriter | None = None,
    input_artifact_writer_override: InputArtifactWriter | None = None,
    session_history_reader_override: SessionHistoryReader | None = None,
) -> FastAPI:
    app = FastAPI(title="Enterprise Data Analyst API", version="0.1.0", lifespan=lifespan)
    app.state.settings_override = settings_override
    app.state.auth_repository_override = auth_repository_override
    app.state.msal_override = msal_override
    app.state.workspace_repository_override = workspace_repository_override
    app.state.upload_service_override = upload_service_override
    app.state.artifact_catalog_override = artifact_catalog_override
    app.state.session_todo_reader_override = session_todo_reader_override
    app.state.session_history_reader_override = session_history_reader_override
    app.state.query_result_writer_override = query_result_writer_override
    app.state.input_artifact_writer_override = input_artifact_writer_override
    app.state.runtime_repository_override = runtime_repository_override
    app.state.event_store_override = event_store_override
    app.state.message_repository_override = message_repository_override
    app.state.hosted_client_override = hosted_client_override
    app.state.fabric_auth_service_override = fabric_auth_service_override
    app.state.fabric_readiness_override = fabric_readiness_override
    app.state.document_readiness_override = document_readiness_override
    app.state.interactive_chat_service_override = interactive_chat_service_override
    app.state.interactive_chat_service = interactive_chat_service_override

    async def liveness() -> HealthStatus:
        return HealthStatus(status="alive")

    async def readiness(response: Response) -> ReadinessStatus:
        active_settings: Settings = app.state.settings
        fabric_status: FabricPackStatus = app.state.fabric_readiness
        documents_status: FabricPackStatus = app.state.document_readiness
        powerbi_status: PowerBiProjectStatus = app.state.powerbi_readiness
        model_ready = active_settings.model_contract_verified and active_settings.tokenizer_calibrated
        product_ready = (
            active_settings.core_ready
            and fabric_status is not FabricPackStatus.FAILED
            and documents_status is not FabricPackStatus.FAILED
        )
        response.status_code = status.HTTP_200_OK if product_ready else status.HTTP_503_SERVICE_UNAVAILABLE
        return ReadinessStatus(
            status="ready" if product_ready else "blocked",
            components={
                "cosmos": "configured",
                "blob": "configured",
                "redis": "configured",
                "foundry": "ready" if model_ready else "blocked",
                "hostedAgent": "ready" if active_settings.hosted_agent_enabled else "blocked",
                "sandbox": "ready" if active_settings.sandbox_image_digest is not None else "blocked",
                "auth": "ready" if active_settings.entra_federation_ready else "blocked",
            },
            feature_packs={
                "core": "ready" if active_settings.core_ready else "blocked",
                "fabric": fabric_status.value,
                "documents": documents_status.value,
                "powerBiProject": powerbi_status.value,
            },
        )

    async def api_not_found(path: str) -> None:
        del path
        raise HTTPException(status_code=404)

    app.add_api_route("/health/live", liveness, methods=["GET"], response_model=HealthStatus, include_in_schema=False)
    app.add_api_route(
        "/health/platform-ready",
        liveness,
        methods=["GET"],
        response_model=HealthStatus,
        include_in_schema=False,
    )
    app.add_api_route(
        "/health/ready", readiness, methods=["GET"], response_model=ReadinessStatus, include_in_schema=False
    )
    install_problem_handlers(app)
    app.include_router(auth_router)
    app.include_router(fabric_auth_router)
    app.include_router(sessions_router)
    app.include_router(session_tasks_router)
    app.include_router(chat_router)
    app.include_router(tasks_router)
    app.include_router(uploads_router)
    app.add_api_route(
        "/api/{path:path}",
        api_not_found,
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
        response_model=ApiProblem,
        include_in_schema=False,
    )
    frontend_dist = _frontend_dist(settings_override)
    if frontend_dist.is_dir():
        frontend = APIRouter()
        frontend.frontend("/", directory=frontend_dist, fallback="index.html")
        app.include_router(frontend)

    return app


configure_logging()
# Before create_app(): the distro patches FastAPI at import time, so a later call misses this app.
configure_telemetry()
app = create_app()
