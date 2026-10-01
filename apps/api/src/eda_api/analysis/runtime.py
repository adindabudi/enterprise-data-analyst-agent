"""Assembly of the in-process analyst runtime from the API's own settings and adapters."""

from __future__ import annotations

import os
import secrets
import socket
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Protocol, cast

from eda_runtime_state.ledger import ExecutionLedgerStore, LedgerContainer
from eda_worker.analysis_services import AnalysisRuntimeHandle, AnalysisRuntimeProvider, RuntimeAnalysisServices
from eda_worker.config import WorkerSettings

from eda_api.config import Settings

from .client import LocalAnalysisClient, TaskAttemptStore
from .streaming import EventAppender, TaskStreams
from .supervisor import ActiveTaskIndex, AnalysisSupervisor, SupervisorLimits

SourceToolsProvider = Callable[[], Sequence[Any]]


def worker_settings(config: Settings) -> WorkerSettings:
    """The worker's settings, taken from the API's where both exist; sandbox placement comes from EDA_SANDBOX_*."""
    return WorkerSettings.model_validate(
        {
            "app_env": config.app_env,
            "managed_identity_client_id": config.managed_identity_client_id,
            "cosmos_endpoint": config.cosmos_endpoint,
            "cosmos_database": config.cosmos_database,
            "cosmos_workspace_container": config.cosmos_workspace_container,
            "cosmos_runtime_container": config.cosmos_runtime_container,
            "blob_account_url": config.blob_account_url,
            "blob_sessions_container": config.blob_sessions_container,
            "redis_url": config.redis_url,
            "redis_stream_ttl_seconds": config.redis_stream_ttl_seconds,
            "redis_stream_max_entries": config.redis_stream_max_entries,
            "foundry_project_endpoint": config.foundry_project_endpoint,
            "foundry_model_deployment": config.foundry_model_deployment,
            "eda_model_profile": config.eda_model_profile,
            "deployment_id": config.deployment_id,
            "worker_image_digest": config.worker_image_digest,
            "sandbox_image_digest": config.sandbox_image_digest,
            "model_contract_path": Path(config.model_contract_path),
            "tokenizer_calibration_path": Path(config.tokenizer_calibration_path),
        }
    )


def supervisor_limits(config: Settings) -> SupervisorLimits:
    return SupervisorLimits(
        max_active_per_replica=config.analysis_max_active_per_replica,
        deployment_limit=config.analysis_deployment_limit,
        queue_depth=config.analysis_queue_depth,
        per_owner_limit=config.analysis_per_owner_limit,
        task_budget=timedelta(minutes=config.analysis_task_budget_minutes),
    )


def replica_identity() -> str:
    # A random suffix keeps a restarted process from mistaking its predecessor's claims for its own.
    replica = os.environ.get("CONTAINER_APP_REPLICA_NAME") or socket.gethostname()
    return f"{replica[:100]}-{secrets.token_hex(6)}"


@dataclass
class InProcessAnalysis:
    provider: AnalysisRuntimeProvider
    services: RuntimeAnalysisServices
    supervisor: AnalysisSupervisor
    client: LocalAnalysisClient

    async def start(self) -> None:
        await self.supervisor.start()

    async def close(self) -> None:
        try:
            await self.supervisor.stop()
        finally:
            await self.provider.close()


class _TaskStore(TaskAttemptStore, ActiveTaskIndex, Protocol):
    pass


def build_in_process_analysis(
    config: Settings,
    *,
    repository: Any,
    runtime_container: Any,
    events: EventAppender | None,
    source_tools: SourceToolsProvider,
    source_context: SourceToolsProvider = tuple,
) -> InProcessAnalysis:
    """Wire the runtime without building it; the supervisor builds it in the background on start."""
    settings = worker_settings(config)

    async def build(value: Any) -> AnalysisRuntimeHandle:
        from eda_worker.runtime import build_analysis_runtime

        return cast(
            AnalysisRuntimeHandle,
            await build_analysis_runtime(
                value,
                source_tools=tuple(source_tools()),
                source_context=tuple(source_context()),
            ),
        )

    provider = AnalysisRuntimeProvider(settings, runtime_factory=build)
    streams = TaskStreams(events, enabled=config.analysis_streaming_enabled)
    services = RuntimeAnalysisServices(provider, delta_sink_factory=streams.sink_for, cancel_reason="user_cancelled")
    store = cast(_TaskStore, repository)
    supervisor = AnalysisSupervisor(
        services=services,
        tasks=store,
        ledger=ExecutionLedgerStore(cast(LedgerContainer, runtime_container)),
        streams=streams,
        replica_id=replica_identity(),
        limits=supervisor_limits(config),
    )
    return InProcessAnalysis(
        provider=provider,
        services=services,
        supervisor=supervisor,
        client=LocalAnalysisClient(supervisor, store),
    )
