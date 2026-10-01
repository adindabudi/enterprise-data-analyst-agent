from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any, cast

from agent_framework import SkillsProvider
from azure.containerapps.sandbox import SandboxGroupClient, endpoint_for_region
from azure.cosmos.aio import CosmosClient
from azure.identity import ManagedIdentityCredential as SyncManagedIdentityCredential
from azure.identity.aio import ManagedIdentityCredential
from azure.storage.blob.aio import BlobServiceClient
from eda_contracts import ArtifactKind, ArtifactRef, ProgressState
from eda_runtime_state.events import EventDraft, RedisEventStore
from eda_runtime_state.redis_auth import create_redis_credential_provider
from eda_runtime_state.tasks import CosmosRuntimeStateRepository, RuntimeStateRepository
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
from .finalization import CoreTaskFinalizer
from .history.provider import ProjectionHistoryProvider
from .history.repository import CosmosProjectionRepository
from .history.todos import SessionOpenTodos
from .model.client import create_foundry_client
from .model.profiles import WorkClass
from .model.startup import load_startup_model_state
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


def create_todo_reporter(
    repository: RuntimeStateRepository,
    event_store: RedisEventStore,
) -> Callable[[str, tuple[dict[str, object], ...]], Awaitable[None]]:
    """Publishes the agent's plan to the task's live stream; like progress, nothing here may escape."""

    async def report_todos(task_id: str, items: tuple[dict[str, object], ...]) -> None:
        try:
            task = await repository.resolve_task(task_id)
            if task is None:
                return
            await event_store.append(
                EventDraft(session_id=task.session_id, task_id=task.id, type="todo.updated", payload=list(items))
            )
        except (ConnectionError, RedisError):
            return
        except Exception:
            logging.getLogger(__name__).exception("todo reporting failed for task %s", task_id)

    return report_todos


async def build_analysis_runtime(
    settings: WorkerSettings,
    *,
    source_tools: Sequence[Any] = (),
    source_context: Sequence[Any] = (),
) -> AnalysisRuntime:
    """Assemble the analyst runtime.

    `source_tools` are the only data-source tools, and the host application owns
    them because it already holds the authorised source adapters. `source_context`
    are the host's context providers for those tools, such as the source's pinned
    schema snapshot.
    """
    injected_source_tools = tuple(source_tools)
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

        task_state = RuntimeTaskStateRepository(runtime_repository, projection_repository)

        tools = [*create_capability_tools(capability_gateway, progress=report_progress), *injected_source_tools]
        harness = create_primary_harness(
            client=foundry_client,
            history_provider=ProjectionHistoryProvider(projection_repository),
            task_state_provider=TaskStateContextProvider(task_state, can_read_source=bool(injected_source_tools)),
            tools=tools,
            tokenizer=startup.tokenizer,
            prompt=startup.prompt,
            skills_provider=skills_provider,
            document_middleware=document_middleware,
            progress=report_progress,
            todo_reporter=create_todo_reporter(runtime_repository, event_store),
            extra_context_providers=tuple(source_context),
        )
        primary_agent = SessionHydratingAgent(
            harness=harness,
            runtime_repository=runtime_repository,
            projection_repository=projection_repository,
            command_repository=runtime_repository,
            model_contract=startup.contract,
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
