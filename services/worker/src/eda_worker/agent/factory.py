from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from agent_framework import (
    SkillsProvider,
    create_harness_agent,
    todos_remaining,
    todos_remaining_message,
)
from eda_worker.context.fabric_readiness import FabricPendingValidationContextProvider
from eda_worker.fabric.readiness import FabricReadiness, FabricReadinessStatus
from eda_worker.history.compaction import hard_ceiling_compactor
from eda_worker.model.profiles import MODEL_PROFILES
from eda_worker.tools.capabilities import ProgressReporter

from .progress import ToolProgressMiddleware
from .prompt_loader import LoadedPrompt

# Foundry's GA web search tool grounds through Bing and supports only OpenAI-family models.
_WEB_SEARCH_BASE_MODELS = frozenset({"gpt-5.6-terra"})

# One pass per open todo, plus room to validate and publish what the last one produced.
MAX_PLAN_ITERATIONS = 8


def create_primary_harness(
    client: Any,
    history_provider: Any,
    task_state_provider: Any,
    tools: list[Any],
    tokenizer: Any,
    prompt: LoadedPrompt,
    *,
    skills_provider: SkillsProvider | None = None,
    document_middleware: Sequence[Any] = (),
    fabric_readiness: FabricReadiness | None = None,
    progress: ProgressReporter | None = None,
):
    active_fabric_readiness = fabric_readiness or FabricReadiness(status=FabricReadinessStatus.DISABLED)
    _validate_fabric_tools(tools, active_fabric_readiness)
    context_providers: list[Any] = [task_state_provider]
    if active_fabric_readiness.status is FabricReadinessStatus.CONFIGURED:
        context_providers.append(FabricPendingValidationContextProvider())
    middleware: list[Any] = list(document_middleware)
    if progress is not None:
        middleware.append(ToolProgressMiddleware(progress))
    web_search_supported = MODEL_PROFILES[prompt.profile_id].expected_base_model in _WEB_SEARCH_BASE_MODELS
    return create_harness_agent(
        client=client,
        name="enterprise-data-analyst",
        description="Private enterprise data analysis agent",
        agent_instructions=prompt.text,
        tools=tools,
        history_provider=history_provider,
        context_providers=context_providers,
        middleware=middleware or None,
        disable_file_memory=True,
        file_access_store=None,
        skills_provider=skills_provider,
        skills_paths=None,
        background_agents=None,
        shell_executor=None,
        disable_web_search=not web_search_supported,
        # Without a predicate the harness runs once, so the agent wrote its plan and stopped on it.
        loop_should_continue=todos_remaining(),
        loop_next_message=todos_remaining_message,
        loop_max_iterations=MAX_PLAN_ITERATIONS,
        before_compaction_strategy=hard_ceiling_compactor(tokenizer),
        after_compaction_strategy=None,
        tokenizer=tokenizer,
        default_options={"store": False, "max_tokens": 64000},
    )


def _validate_fabric_tools(tools: list[Any], readiness: FabricReadiness) -> None:
    names = [getattr(tool, "name", None) for tool in tools]
    has_query_fabric = "query_fabric" in names
    if readiness.status is FabricReadinessStatus.FAILED:
        raise RuntimeError("Fabric readiness failed")
    if readiness.status is FabricReadinessStatus.READY and not has_query_fabric:
        raise RuntimeError("ready Fabric requires query_fabric")
    if readiness.status is not FabricReadinessStatus.READY and has_query_fabric:
        raise RuntimeError("query_fabric is present while Fabric is not ready")
