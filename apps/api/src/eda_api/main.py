from __future__ import annotations

import logging
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

from eda_api.analysis.client import RoutingTaskClient
from eda_api.analysis.runtime import InProcessAnalysis, build_in_process_analysis
from eda_api.analysis.source_context import SourceSnapshotContextProvider
from eda_api.analysis.source_tools import SourceTools
from eda_api.auth.msal_client import MsalAuthClient
from eda_api.auth.repository import AuthRepository, CosmosAuthRepository
from eda_api.auth.routes import router as auth_router
from eda_api.config import DEFAULT_FRONTEND_DIST, Settings
from eda_api.fabric_auth import FabricAuthCoordinator, FabricAuthorizationCodeClient
from eda_api.fabric_auth.capacity import CapacityMonitor, CapacityStatus
from eda_api.fabric_auth.eventhouse import FabricEventhouseQueryService
from eda_api.fabric_auth.graph import FabricGraphQueryService
from eda_api.fabric_auth.msal_client import FabricAccessTokenClient
from eda_api.fabric_auth.ontology import OntologyEndpointProbe
from eda_api.fabric_auth.routes import router as fabric_auth_router
from eda_api.fabric_auth.routes import source_router as fabric_source_router
from eda_api.fabric_auth.snapshot import SnapshotUnavailableError, read_snapshot
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

logger = logging.getLogger(__name__)


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
    fabric_capacity: CapacityStatus | None = None
    eventhouse_query: FabricEventhouseQueryService | None = None
    source_wired = False
    analysis: InProcessAnalysis | None = None
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
        if hosted_client is None and (config.hosted_agent_enabled or not config.analysis_runtime_enabled):
            if credential is None:
                from azure.identity.aio import DefaultAzureCredential

                if config.managed_identity_client_id is None:
                    credential = DefaultAzureCredential()
                else:
                    credential = DefaultAzureCredential(
                        managed_identity_client_id=str(config.managed_identity_client_id)
                    )
            hosted_client = HostedResponsesClient(config.hosted_responses_endpoint, credential)
        # Source tools are assembled once the Fabric adapters exist; the runtime is built after that.
        analysis_tools: list[Any] = []
        analysis_context: list[Any] = []
        task_client: HostedTaskClient | None = cast(HostedTaskClient | None, hosted_client)
        if config.analysis_runtime_enabled:
            if database is None:
                raise ValueError("the analysis runtime requires the managed Cosmos database")
            analysis = build_in_process_analysis(
                config,
                repository=runtime_repository,
                runtime_container=database.get_container_client(config.cosmos_runtime_container),
                events=cast(Any, event_store),
                source_tools=lambda: analysis_tools,
                source_context=lambda: analysis_context,
            )
            task_client = RoutingTaskClient(analysis.client, legacy=task_client)
        if task_client is None:
            raise ValueError("no task execution client is configured")
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
        app.state.hosted_client = task_client
        app.state.analysis = analysis
        app.state.task_service = TaskService(
            cast(RuntimeStateRepository, runtime_repository),
            cast(MessageRepository, message_repository),
            task_client,
            cast(TaskEventStore | None, event_store),
            cast(QueryResultWriter | None, query_result_writer),
            uploads=cast(UploadService, uploads),
            input_artifact_writer=cast(InputArtifactWriter | None, input_artifact_writer),
        )
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
                    # A paused capacity is paused for every caller, so the reads and the status share one view of it.
                    capacity_monitor = CapacityMonitor()
                    source_alias, source_target = next(iter(config.fabric_ontologies.items()))
                    fabric_capacity = CapacityStatus(
                        capacity_monitor,
                        tokens=fabric_access_token_client,
                        source=OntologyEndpointProbe(source_target, capacity=capacity_monitor),
                    )
                    try:
                        # Loaded at creation, as the agent's instructions: nothing discovers the schema at run time.
                        source_snapshot = await read_snapshot(
                            database_client.get_container_client(config.cosmos_runtime_container),
                            source_alias,
                            source_target,
                        )
                    except SnapshotUnavailableError as error:
                        logger.warning("Fabric source queries are unavailable: %s", error)
                    else:
                        graph_query = FabricGraphQueryService(
                            source_target, token_provider=fabric_access_token_client, capacity=capacity_monitor
                        )
                        if source_target.kql_database_id is not None:
                            eventhouse_query = FabricEventhouseQueryService(
                                source_target, token_provider=fabric_access_token_client, capacity=capacity_monitor
                            )
                        elif source_snapshot.has_time_series:
                            logger.warning("the Fabric source binds time series but no kqlDatabaseId is configured")
                        analysis_tools.extend(
                            SourceTools(
                                alias=source_alias,
                                description=source_target.description,
                                graph=graph_query,
                                timeseries=eventhouse_query,
                                tasks=cast(Any, runtime_repository),
                                ledger=cast(Any, runtime_repository),
                                writer=cast(Any, query_result_writer),
                                events=cast(Any, event_store),
                            ).tools()
                        )
                        analysis_context.append(
                            SourceSnapshotContextProvider(
                                snapshot=source_snapshot,
                                description=source_target.description,
                                timeseries=eventhouse_query is not None,
                                tasks=cast(Any, runtime_repository),
                                tokens=fabric_access_token_client,
                            )
                        )
                        source_wired = True
            app.state.fabric_auth_service = fabric_auth_service
        else:
            app.state.fabric_auth_service = None
        app.state.fabric_capacity = fabric_capacity
        # Fabric can be queried from a configured same-tenant source long before acceptance promotes it to ready.
        app.state.fabric_chat_query = source_wired
        if analysis is not None:
            await analysis.start()
        app.state.started = True
        yield
    finally:
        if analysis is not None:
            await analysis.close()
        if eventhouse_query is not None:
            await eventhouse_query.aclose()
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
    app.state.analysis = None

    async def liveness() -> HealthStatus:
        return HealthStatus(status="alive")

    async def platform_readiness(response: Response) -> HealthStatus:
        # The platform routes traffic only to a replica whose analyst runtime can take tasks, so a
        # revision that cannot build it never replaces the one that can.
        analysis: InProcessAnalysis | None = app.state.analysis
        if analysis is not None and analysis.supervisor.readiness() != "ready":
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return HealthStatus(status=analysis.supervisor.readiness())
        return HealthStatus(status="alive")

    async def readiness(response: Response) -> ReadinessStatus:
        active_settings: Settings = app.state.settings
        fabric_status: FabricPackStatus = app.state.fabric_readiness
        documents_status: FabricPackStatus = app.state.document_readiness
        powerbi_status: PowerBiProjectStatus = app.state.powerbi_readiness
        analysis: InProcessAnalysis | None = app.state.analysis
        model_ready = active_settings.model_contract_verified and active_settings.tokenizer_calibrated
        analysis_state = analysis.supervisor.readiness() if analysis is not None else "blocked"
        product_ready = (
            active_settings.core_ready
            and analysis_state == "ready"
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
                "analysisRuntime": analysis_state,
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
        platform_readiness,
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
    app.include_router(fabric_source_router)
    app.include_router(sessions_router)
    app.include_router(session_tasks_router)
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
