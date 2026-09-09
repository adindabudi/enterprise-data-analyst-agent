from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any, Protocol, cast

from agent_framework import FunctionInvocationContext, FunctionTool, tool
from eda_contracts import ProgressState
from eda_worker.fabric.contracts import (
    FabricPrincipal,
    FabricQueryOperation,
    FabricQueryResult,
    FabricSourceGuide,
    build_query_fabric_description,
)
from eda_worker.sandbox_contract import EXECUTION_GUIDANCE
from eda_worker.web_artifacts.builder import build_web_execution
from pydantic import BaseModel

from .contracts import (
    BuildWebArtifactOperation,
    CapabilityResult,
    CapabilityStatus,
    ExecuteSandboxOperation,
    InspectArtifactOperation,
    PublishArtifactOperation,
    ValidateArtifactOperation,
    progress_state_for,
)


class CapabilityGateway(Protocol):
    async def is_cancelled(self, task_id: str) -> bool: ...

    async def inspect(self, task_id: str, operation: InspectArtifactOperation) -> CapabilityResult: ...

    async def execute(self, task_id: str, operation: ExecuteSandboxOperation) -> CapabilityResult: ...

    async def validate(self, task_id: str, operation: ValidateArtifactOperation) -> CapabilityResult: ...

    async def publish(self, task_id: str, operation: PublishArtifactOperation) -> CapabilityResult: ...


class FabricCapabilityGateway(Protocol):
    async def query(
        self,
        task_id: str,
        invocation_id: str,
        principal: FabricPrincipal,
        operation: BaseModel,
    ) -> FabricQueryResult: ...


ProgressReporter = Callable[[str, str, str | None, ProgressState], Awaitable[None]]


def _task_id(context: FunctionInvocationContext) -> str:
    raw_kwargs = cast(object, getattr(context, "kwargs", {}))
    kwargs: dict[str, object] = cast(dict[str, object], raw_kwargs) if isinstance(raw_kwargs, dict) else {}
    task_id: object = kwargs.get("task_id")
    if not isinstance(task_id, str) or not task_id.startswith("task_"):
        raise ValueError("tool invocation is missing its trusted task scope")
    return task_id


def create_capability_tools(
    gateway: CapabilityGateway,
    fabric_gateway: FabricCapabilityGateway | None = None,
    *,
    source_guides: tuple[FabricSourceGuide, ...] = (),
    fabric_operation_model: type[BaseModel] = FabricQueryOperation,
    progress: ProgressReporter | None = None,
) -> list[FunctionTool]:
    fabric_tools: list[FunctionTool] = []
    if fabric_gateway is None:
        if source_guides:
            raise ValueError("Fabric source guides require a Fabric gateway")
    else:
        description = build_query_fabric_description(source_guides)

        @tool(
            name="query_fabric",
            description=description,
            schema=fabric_operation_model,
            approval_mode="never_require",
            additional_properties={"side_effect": "external_read"},
        )
        async def query_fabric(context: FunctionInvocationContext, **arguments: Any) -> dict[str, Any]:
            task_id = _task_id(context)
            if await gateway.is_cancelled(task_id):
                return FabricQueryResult(
                    status="cancelled",
                    summary="Task cancellation was requested.",
                ).model_dump(mode="json")
            invocation_id, principal = _fabric_context(context)
            operation = fabric_operation_model.model_validate(arguments)
            source_alias = _fabric_source_alias(operation)
            if source_alias not in {guide.alias for guide in source_guides}:
                raise ValueError("requested Fabric source is not configured")
            if progress is not None:
                await progress(task_id, "Querying Fabric", "Reading the configured enterprise source.", "running")
            result = await fabric_gateway.query(task_id, invocation_id, principal, operation)
            if progress is not None:
                refused = result.status not in {"ok", "cancelled"}
                await progress(
                    task_id,
                    "Fabric query finished",
                    f"Enterprise source query returned {result.status}.",
                    "failed" if refused else "completed",
                )
            if "ontology" in operation.__class__.model_fields and result.status not in {
                "ok",
                "authorization_error",
                "cancelled",
            }:
                raise ValueError("ontology result status is invalid")
            return result.model_dump(mode="json")

        fabric_tools.append(query_fabric)

    @tool(
        name="inspect_artifact",
        description="Inspect bounded metadata, schema, or a small artifact sample.",
        schema=InspectArtifactOperation,
        approval_mode="never_require",
        additional_properties={"side_effect": "read_only"},
    )
    async def inspect_artifact(context: FunctionInvocationContext, **arguments: Any) -> dict[str, Any]:
        result = await gateway.inspect(_task_id(context), InspectArtifactOperation.model_validate(arguments))
        return result.model_dump(mode="json")

    @tool(
        name="execute_in_sandbox",
        description=EXECUTION_GUIDANCE,
        schema=ExecuteSandboxOperation.model_json_schema(),
        approval_mode="never_require",
        additional_properties={"side_effect": "session_local"},
    )
    async def execute_in_sandbox(context: FunctionInvocationContext, **arguments: Any) -> dict[str, Any]:
        task_id = _task_id(context)
        if await gateway.is_cancelled(task_id):
            return CapabilityResult(
                status=CapabilityStatus.CANCELLED, summary="Task cancellation was requested."
            ).model_dump(mode="json")
        operation = ExecuteSandboxOperation.model_validate(arguments)
        if progress is not None:
            runtime_label = "Python" if operation.runtime.value == "python" else "JavaScript"
            await progress(task_id, "Running analysis script", f"{runtime_label} sandbox execution started.", "running")
        result = await gateway.execute(task_id, operation)
        if progress is not None:
            await progress(
                task_id,
                "Analysis script finished",
                _bounded_progress_detail(result.summary),
                progress_state_for(result.status),
            )
        return result.model_dump(mode="json")

    @tool(
        name="build_web_artifact",
        description=(
            "Build a self-contained interactive React HTML dashboard using the mandatory reviewed "
            "web-artifacts-builder guidance and the offline baked toolchain."
        ),
        schema=BuildWebArtifactOperation,
        approval_mode="never_require",
        additional_properties={"side_effect": "session_local"},
    )
    async def build_web_artifact(context: FunctionInvocationContext, **arguments: Any) -> dict[str, Any]:
        task_id = _task_id(context)
        if await gateway.is_cancelled(task_id):
            return CapabilityResult(
                status=CapabilityStatus.CANCELLED, summary="Task cancellation was requested."
            ).model_dump(mode="json")
        operation = BuildWebArtifactOperation.model_validate(arguments)
        if progress is not None:
            await progress(task_id, "Building interactive dashboard", "Offline web bundling started.", "running")
        result = await gateway.execute(task_id, build_web_execution(operation))
        if progress is not None:
            await progress(
                task_id,
                "Interactive dashboard build finished",
                _bounded_progress_detail(result.summary),
                progress_state_for(result.status),
            )
        return result.model_dump(mode="json")

    @tool(
        name="validate_artifact",
        description="Run the deterministic validator for a generated artifact.",
        schema=ValidateArtifactOperation,
        approval_mode="never_require",
        additional_properties={"side_effect": "session_local"},
    )
    async def validate_artifact(context: FunctionInvocationContext, **arguments: Any) -> dict[str, Any]:
        task_id = _task_id(context)
        if await gateway.is_cancelled(task_id):
            return CapabilityResult(
                status=CapabilityStatus.CANCELLED, summary="Task cancellation was requested."
            ).model_dump(mode="json")
        result = await gateway.validate(task_id, ValidateArtifactOperation.model_validate(arguments))
        return result.model_dump(mode="json")

    @tool(
        name="publish_artifact",
        description="Publish one validated artifact version to the private analysis.",
        schema=PublishArtifactOperation,
        approval_mode="never_require",
        additional_properties={"side_effect": "session_local_publication"},
    )
    async def publish_artifact(context: FunctionInvocationContext, **arguments: Any) -> dict[str, Any]:
        task_id = _task_id(context)
        if await gateway.is_cancelled(task_id):
            return CapabilityResult(
                status=CapabilityStatus.CANCELLED, summary="Task cancellation was requested."
            ).model_dump(mode="json")
        result = await gateway.publish(task_id, PublishArtifactOperation.model_validate(arguments))
        return result.model_dump(mode="json")

    return [
        *fabric_tools,
        inspect_artifact,
        execute_in_sandbox,
        build_web_artifact,
        validate_artifact,
        publish_artifact,
    ]


def _fabric_context(context: FunctionInvocationContext) -> tuple[str, FabricPrincipal]:
    raw_kwargs = cast(object, getattr(context, "kwargs", {}))
    kwargs: dict[str, object] = cast(dict[str, object], raw_kwargs) if isinstance(raw_kwargs, dict) else {}
    raw_metadata = cast(object, getattr(context, "metadata", {}))
    metadata: dict[str, object] = cast(dict[str, object], raw_metadata) if isinstance(raw_metadata, dict) else {}
    invocation_id = metadata.get("call_id")
    principal = kwargs.get("principal")
    if not isinstance(invocation_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", invocation_id):
        raise ValueError("Fabric invocation is missing its trusted invocation ID")
    if not isinstance(principal, FabricPrincipal):
        raise ValueError("Fabric invocation is missing its trusted principal")
    return invocation_id, principal


def _fabric_source_alias(operation: BaseModel) -> str:
    values = operation.model_dump(mode="python")
    source_alias = values.get("semantic_model", values.get("ontology"))
    if not isinstance(source_alias, str):
        raise ValueError("Fabric operation is missing its source alias")
    return source_alias


create_core_tools = create_capability_tools


def _bounded_progress_detail(value: str) -> str:
    return value if len(value) <= 240 else f"{value[:237]}..."
