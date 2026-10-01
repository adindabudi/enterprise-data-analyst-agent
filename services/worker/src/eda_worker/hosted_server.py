"""The legacy hosted-agent entry point, kept only while its existing tasks drain.

New tasks run in-process inside the API application. This host wraps the same
services (`analysis_services`) in the Foundry Responses hosting server so work
it already accepted can finish.
"""

from __future__ import annotations

from typing import Any

from agent_framework_foundry_hosting import ResponsesHostServer
from azure.ai.agentserver.responses import ResponsesServerOptions

from .analysis_services import (
    RUN_INSTRUCTION,
    AnalysisAgentResponseError,
    AnalysisRuntimeHandle,
    AnalysisRuntimeProvider,
    ClosableResources,
    RuntimeAnalysisServices,
    RuntimeFactory,
    response_text,
)
from .hosted_workflow import build_hosted_workflow

# Names the hosted agent and its tests have always used.
AnalysisRuntime = AnalysisRuntimeHandle
HostedRuntimeProvider = AnalysisRuntimeProvider
HostedAnalysisServices = RuntimeAnalysisServices
_response_text = response_text

__all__ = [
    "RUN_INSTRUCTION",
    "AnalysisAgentResponseError",
    "AnalysisRuntime",
    "ClosableResources",
    "HostedAnalysisServices",
    "HostedRuntimeProvider",
    "RuntimeFactory",
    "create_hosted_server",
    "run_hosted_server",
]


def create_hosted_server(
    settings: Any,
    *,
    runtime_factory: RuntimeFactory | None = None,
) -> ResponsesHostServer:
    provider = AnalysisRuntimeProvider(settings, runtime_factory=runtime_factory)
    services = RuntimeAnalysisServices(provider)
    workflow_agent = build_hosted_workflow(services).as_agent(
        id="enterprise-data-analyst-long-job",
        name="enterprise-data-analyst-long-job",
        description="Deterministic long-running enterprise data analysis workflow",
    )
    server = ResponsesHostServer(
        workflow_agent,
        options=ResponsesServerOptions(resilient_background=True),
        log_level="INFO",
    )
    server.shutdown_handler(provider.close)
    return server


def run_hosted_server(settings: Any) -> None:
    create_hosted_server(settings).run()
