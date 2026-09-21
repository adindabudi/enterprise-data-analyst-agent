from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from agent_framework import SkillsProvider
from azure.containerapps.sandbox import SandboxGroupClient, endpoint_for_region
from azure.cosmos.aio import CosmosClient
from azure.identity import ManagedIdentityCredential as SyncManagedIdentityCredential
from azure.identity.aio import ManagedIdentityCredential
from azure.storage.blob.aio import BlobServiceClient
from eda_contracts import ArtifactKind, ArtifactRef, ProgressState
from eda_fabric_auth import CosmosFabricGrantRepository, EnvelopeCipher, FabricProvider, KeyVaultKeyWrapper
from eda_runtime_state.events import EventDraft, RedisEventStore
from eda_runtime_state.redis_auth import create_redis_credential_provider
from eda_runtime_state.tasks import CosmosRuntimeStateRepository, RuntimeStateRepository
from pydantic import BaseModel
from redis.asyncio import Redis
from redis.exceptions import RedisError

from .activities import create_activities
from .agent.factory import create_primary_harness
from .agent.session_adapter import SessionHydratingAgent
from .config import WorkerSettings
from .context.repository import RuntimeTaskStateRepository
from .context.task_state import TaskStateContextProvider
from .documents.config import DocumentSettings
from .documents.readiness import (
    SKILL_NAMES,
    DocumentReadinessStatus,
    load_document_runtime_readiness,
)
from .documents.runner import DocumentSkillScriptRunner, DocumentTaskScopeMiddleware
from .fabric.config import FabricSettings
from .fabric.contracts import FabricPrincipal, FabricQueryOperation, FabricSourceGuide
from .fabric.gateway import ArtifactFabricResultStore, FabricIQGateway
from .fabric.mcp_client import FabricMcpClient
from .fabric.ontology.capability import CosmosOntologyQueryRepository, FabricOntologyCapabilityGateway
from .fabric.ontology.contracts import FabricOntologyQueryOperation
from .fabric.ontology.gateway import FabricOntologyGateway
from .fabric.ontology.mcp_client import OntologyMcpClient
from .fabric.ontology.readiness import (
    OntologyProviderRuntimeContract,
    load_ontology_runtime_state,
    ontology_provider_contract_sha256,
)
from .fabric.planner import FabricAnalystPlanner, FabricPlanningClient
from .fabric.provenance import CosmosFabricQueryRepository, FabricQueryRecorder
from .fabric.readiness import (
    FabricModelIdentity,
    FabricReadiness,
    FabricReadinessStatus,
    FabricRuntimeState,
    load_fabric_runtime_state,
)
from .fabric.token_provider import FabricAccessTokenProvider
from .finalization import CoreTaskFinalizer
from .history.provider import ProjectionHistoryProvider
from .history.repository import CosmosProjectionRepository
from .history.todos import SessionOpenTodos
from .model.client import create_foundry_client
from .model.profiles import WorkClass
from .model.startup import StartupModelState, load_startup_model_state
from .output_planning import OutputContractPlanner, OutputPlanningClient
from .sandbox.aca_client import AcaSandboxClient
from .sandbox.gateway import CosmosBlobArtifactGatewayStore, DynamicSessionCapabilityGateway
from .tools.capabilities import create_capability_tools
from .web_artifacts.readiness import WebArtifactSettings, require_web_artifact_skill


@dataclass
class AnalysisRuntime:
    repository: RuntimeStateRepository
    primary_agent: Any
    activities: Any
    resources: AsyncExitStack
    output_planner: OutputContractPlanner


def create_progress_reporter(
    repository: RuntimeStateRepository,
    event_store: RedisEventStore,
) -> Callable[[str, str, str | None, ProgressState], Awaitable[None]]:
    """Nothing here may escape.

    This runs around a tool call, so an exception raised while reporting would either fail a tool
    that succeeded or, inside an except block, replace the tool's own failure with a story about
    the reporter.
    """

    async def report_progress(task_id: str, milestone: str, detail: str | None, state: ProgressState) -> None:
        try:
            task = await repository.resolve_task(task_id)
            if task is None:
                return
            await event_store.append(
                EventDraft(
                    session_id=task.session_id,
                    task_id=task.id,
                    type="analysis_progress",
                    payload={"milestone": milestone, "detail": detail, "state": state},
                )
            )
        except (ConnectionError, RedisError):
            return
        except Exception:
            logging.getLogger(__name__).exception("progress reporting failed for task %s", task_id)

    return report_progress


async def build_analysis_runtime(settings: WorkerSettings) -> AnalysisRuntime:
    startup = load_startup_model_state(settings)
    resources = AsyncExitStack()
    try:
        managed_identity_client_id = (
            str(settings.managed_identity_client_id) if settings.managed_identity_client_id is not None else None
        )
        credential = ManagedIdentityCredential(client_id=managed_identity_client_id)
        resources.push_async_callback(credential.close)
        sync_credential = SyncManagedIdentityCredential(client_id=managed_identity_client_id)
        resources.callback(sync_credential.close)

        cosmos_client = CosmosClient(str(settings.cosmos_endpoint), credential=credential)
        resources.push_async_callback(cosmos_client.close)
        database = cosmos_client.get_database_client(settings.cosmos_database)
        workspace_container = database.get_container_client(settings.cosmos_workspace_container)
        runtime_container = database.get_container_client(settings.cosmos_runtime_container)
        runtime_repository = CosmosRuntimeStateRepository(workspace_container, runtime_container)
        projection_repository = CosmosProjectionRepository(workspace_container)

        blob_service = BlobServiceClient(account_url=str(settings.blob_account_url), credential=credential)
        resources.push_async_callback(blob_service.close)
        session_blobs = blob_service.get_container_client(settings.blob_sessions_container)

        redis_client = Redis.from_url(  # type: ignore[reportUnknownMemberType]
            settings.redis_url,
            decode_responses=True,
            credential_provider=create_redis_credential_provider(managed_identity_client_id),
        )
        resources.push_async_callback(redis_client.aclose)
        event_store = RedisEventStore(
            redis_client,
            ttl_seconds=settings.redis_stream_ttl_seconds,
            max_entries=settings.redis_stream_max_entries,
        )

        report_progress = create_progress_reporter(runtime_repository, event_store)

        foundry_client, foundry_credential = create_foundry_client(settings, startup.tokenizer)
        resources.push_async_callback(foundry_credential.close)
        resources.push_async_callback(foundry_client.project_client.close)

        sandbox_group_client = SandboxGroupClient(
            endpoint_for_region(settings.sandbox_region),
            sync_credential,
            subscription_id=str(settings.sandbox_subscription_id),
            resource_group=settings.sandbox_resource_group,
            sandbox_group=settings.sandbox_group,
        )
        session_client = AcaSandboxClient(
            cast(Any, sandbox_group_client),
            disk_image_id=settings.sandbox_disk_image_id,
        )
        resources.push_async_callback(session_client.close)
        artifact_store = CosmosBlobArtifactGatewayStore(
            runtime_repository,
            workspace_container,
            session_blobs,
        )
        capability_gateway = DynamicSessionCapabilityGateway(
            session_client,
            runtime_repository,
            artifact_store,
        )
        documents_settings = DocumentSettings()
        web_skill_path = require_web_artifact_skill(WebArtifactSettings())
        documents_state = await load_document_runtime_readiness(
            cast(Any, runtime_container),
            settings=documents_settings,
            deployment_id=settings.deployment_id,
            worker_image_digest=settings.worker_image_digest,
            sandbox_image_digest=settings.sandbox_image_digest,
        )
        if documents_state.status is DocumentReadinessStatus.FAILED:
            # An optional pack must not take the analysis runtime down with it.
            logging.getLogger(__name__).error(
                "Document Pack readiness failed; continuing without document skills",
                extra={"deployment_id": settings.deployment_id},
            )
        skill_paths = [web_skill_path]
        skill_script_runner = None
        document_middleware: tuple[DocumentTaskScopeMiddleware, ...] = ()
        if documents_state.status is DocumentReadinessStatus.READY:

            async def load_document_bundle(task_id: str, skill_name: str) -> ArtifactRef:
                if skill_name not in set(SKILL_NAMES):
                    raise ValueError("document skill bundle name is invalid")
                bundle_path = documents_settings.skill_root / f"{skill_name}.zip"
                return await artifact_store.persist_bytes(
                    task_id,
                    ArtifactKind.INPUT,
                    f"document-skill-{skill_name}.zip",
                    bundle_path.read_bytes(),
                )

            skill_script_runner = DocumentSkillScriptRunner(
                gateway=capability_gateway,
                bundle_root=documents_settings.skill_root,
                bundle_loader=load_document_bundle,
                progress=report_progress,
            )
            # These bundles are first-party and their hashes are checked before readiness flips,
            # and there is no human on the other end of an approval prompt here.
            skill_paths.extend(documents_settings.skill_root / name for name in SKILL_NAMES)
            document_middleware = (DocumentTaskScopeMiddleware(),)
        skills_provider = SkillsProvider.from_paths(
            skill_paths,
            script_runner=skill_script_runner,
            script_extensions=(".py",),
            disable_load_skill_approval=True,
            disable_read_skill_resource_approval=True,
            disable_run_skill_script_approval=True,
        )

        fabric_settings = FabricSettings()
        active_model = _fabric_model_identity(startup)
        semantic_contract = None
        ontology_contract: OntologyProviderRuntimeContract | None = None
        if fabric_settings.enabled and fabric_settings.provider is FabricProvider.ONTOLOGY:
            ontology_state = await load_ontology_runtime_state(
                cast(Any, runtime_container),
                enabled=True,
                catalog=fabric_settings.ontologies,
                active_model=active_model,
                deployment_id=settings.deployment_id,
            )
            fabric_readiness = ontology_state.readiness
            ontology_contract = ontology_state.contract
        else:
            semantic_state = await _load_semantic_fabric_state(
                fabric_settings=fabric_settings,
                runtime_container=runtime_container,
                startup=startup,
                deployment_id=settings.deployment_id,
            )
            fabric_readiness = semantic_state.readiness
            semantic_contract = semantic_state.contract
        fabric_readiness = isolate_optional_fabric_failure(fabric_readiness)
        fabric_gateway: Any = None
        source_guides: tuple[FabricSourceGuide, ...] = ()
        fabric_operation_model: type[BaseModel] = FabricQueryOperation
        if fabric_readiness.status is FabricReadinessStatus.READY:
            if (
                fabric_settings.tenant_id is None
                or fabric_settings.client_id is None
                or fabric_settings.key_vault_url is None
                or fabric_settings.signing_certificate_name is None
                or fabric_settings.cache_wrap_key_name is None
            ):
                raise RuntimeError("ready selected Fabric configuration is incomplete")
            grant_repository = CosmosFabricGrantRepository(
                cast(Any, database.get_container_client(settings.cosmos_auth_container)),
                cast(Any, database.get_container_client(settings.cosmos_fabric_auth_container)),
            )
            key_vault_url = str(fabric_settings.key_vault_url).rstrip("/")
            key_wrapper = KeyVaultKeyWrapper(
                f"{key_vault_url}/keys/{fabric_settings.cache_wrap_key_name}",
                credential,
            )
            resources.push_async_callback(key_wrapper.close)
            cipher = EnvelopeCipher(key_wrapper)
            token_provider = FabricAccessTokenProvider(
                repository=grant_repository,
                cipher=cipher,
                fabric_tenant_id=fabric_settings.tenant_id,
                fabric_client_id=fabric_settings.client_id,
                key_vault_url=key_vault_url,
                signing_certificate_name=fabric_settings.signing_certificate_name,
                credential=sync_credential,
            )
            resources.callback(token_provider.close)
            if fabric_settings.provider is FabricProvider.SEMANTIC_MODEL:
                contract = semantic_contract
                if contract is None:
                    raise RuntimeError("ready semantic Fabric contract is unavailable")
                expected_tools = tuple(
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "inputSchema": tool.input_schema,
                    }
                    for tool in contract.tools
                )

                def planner_factory(principal: FabricPrincipal) -> FabricAnalystPlanner:
                    async def owner_token() -> str:
                        token = await token_provider.acquire_for_principal(
                            principal=principal,
                            provider=FabricProvider.SEMANTIC_MODEL,
                        )
                        return token.token

                    mcp_client = FabricMcpClient(
                        token_provider=owner_token,
                        expected_tools=expected_tools,
                    )
                    return FabricAnalystPlanner(
                        cast(FabricPlanningClient, foundry_client),
                        mcp_client,
                        model_options=startup.contract.options_for(WorkClass.ANALYSIS),
                        max_turns=fabric_settings.max_analyst_turns,
                    )

                fabric_gateway = FabricIQGateway(
                    planner_factory=planner_factory,
                    runtime=runtime_repository,
                    result_store=ArtifactFabricResultStore(artifact_store),
                    models=fabric_settings.models,
                    evidence_recorder=FabricQueryRecorder(
                        artifact_store=artifact_store,
                        repository=CosmosFabricQueryRepository(workspace_container),
                    ),
                )
                source_guides = tuple(
                    FabricSourceGuide(
                        alias=alias,
                        description=target.description,
                        vocabulary=target.routing_terms,
                    )
                    for alias, target in sorted(fabric_settings.models.items())
                )
            elif fabric_settings.provider is FabricProvider.ONTOLOGY:
                contract = ontology_contract
                if contract is None:
                    raise RuntimeError("ready ontology Fabric contract is unavailable")
                grounding_digests = {alias.alias: alias.grounding_sha256 for alias in contract.aliases}

                def ontology_gateway_factory(principal: FabricPrincipal) -> FabricOntologyGateway:
                    async def owner_token() -> str:
                        token = await token_provider.acquire_for_principal(
                            principal=principal,
                            provider=FabricProvider.ONTOLOGY,
                        )
                        return token.token

                    return FabricOntologyGateway(
                        catalog=fabric_settings.ontologies,
                        client_factory=lambda target: OntologyMcpClient(
                            target,
                            token_provider=owner_token,
                        ),
                        expected_grounding_digests=grounding_digests,
                    )

                fabric_gateway = FabricOntologyCapabilityGateway(
                    catalog=fabric_settings.ontologies,
                    gateway_factory=ontology_gateway_factory,
                    runtime=runtime_repository,
                    artifacts=artifact_store,
                    evidence=CosmosOntologyQueryRepository(workspace_container),
                    provider_contract_digest=ontology_provider_contract_sha256(contract),
                )
                source_guides = tuple(alias.model_guide() for alias in contract.aliases)
                fabric_operation_model = FabricOntologyQueryOperation
            else:
                raise RuntimeError("ready Fabric provider is unsupported")

        task_state = RuntimeTaskStateRepository(runtime_repository, projection_repository)

        tools = create_capability_tools(
            capability_gateway,
            fabric_gateway,
            source_guides=source_guides,
            fabric_operation_model=fabric_operation_model,
            progress=report_progress,
        )
        harness = create_primary_harness(
            client=foundry_client,
            history_provider=ProjectionHistoryProvider(projection_repository),
            task_state_provider=TaskStateContextProvider(
                task_state,
                # query_fabric is registered only at READY, so this is also "has a source tool".
                can_read_source=fabric_readiness.status is FabricReadinessStatus.READY,
            ),
            tools=tools,
            tokenizer=startup.tokenizer,
            prompt=startup.prompt,
            skills_provider=skills_provider,
            document_middleware=document_middleware,
            fabric_readiness=fabric_readiness,
            progress=report_progress,
        )
        primary_agent = SessionHydratingAgent(
            harness=harness,
            runtime_repository=runtime_repository,
            projection_repository=projection_repository,
            command_repository=runtime_repository,
            model_contract=startup.contract,
            product_audience=settings.entra_client_id,
        )
        finalizer = CoreTaskFinalizer(
            runtime_repository,
            artifact_store,
            projection_repository,
            capability_gateway,
            SessionOpenTodos(projection_repository),
        )
        activities = create_activities(
            runtime_repository,
            event_store,
            validate_outputs_handler=finalizer.validate_outputs,
            complete_chat_handler=finalizer.complete_chat,
            publish_outputs_handler=finalizer.publish_outputs,
            cancel_task_handler=finalizer.cancel_task,
            fail_task_handler=finalizer.fail_task,
        )

        return AnalysisRuntime(
            repository=runtime_repository,
            primary_agent=primary_agent,
            activities=activities,
            resources=resources.pop_all(),
            output_planner=OutputContractPlanner(
                cast(OutputPlanningClient, foundry_client),
                runtime_repository,
                task_state,
                model_options=startup.contract.options_for(WorkClass.CLARIFICATION),
            ),
        )
    except BaseException:
        await resources.aclose()
        raise


async def _load_semantic_fabric_state(
    *,
    fabric_settings: FabricSettings,
    runtime_container: object,
    startup: StartupModelState,
    deployment_id: str,
) -> FabricRuntimeState:
    selected = fabric_settings.enabled and fabric_settings.provider is FabricProvider.SEMANTIC_MODEL
    return await load_fabric_runtime_state(
        cast(Any, runtime_container),
        enabled=selected,
        fabric_tenant_id=fabric_settings.tenant_id,
        active_model=_fabric_model_identity(startup),
        deployment_id=deployment_id,
        now=datetime.now(UTC),
    )


def isolate_optional_fabric_failure(readiness: FabricReadiness) -> FabricReadiness:
    if readiness.status is not FabricReadinessStatus.FAILED:
        return readiness
    logging.getLogger(__name__).error("Fabric Pack readiness failed; continuing without Fabric tools")
    return FabricReadiness(status=FabricReadinessStatus.DISABLED)


def _fabric_model_identity(startup: StartupModelState) -> FabricModelIdentity:
    contract = startup.contract
    return FabricModelIdentity(
        modelProfile=contract.model_profile.value,
        modelDeployment=contract.deployment,
        servedModel=contract.base_model,
        servedSnapshot=contract.base_model_snapshot,
        promptVersion=contract.prompt_version,
        promptSha256=contract.prompt_sha256,
        requestOptionsSha256=contract.request_options_sha256,
    )
