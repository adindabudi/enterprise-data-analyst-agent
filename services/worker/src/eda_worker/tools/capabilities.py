from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol, cast

from agent_framework import FunctionInvocationContext, FunctionTool, tool
from eda_contracts import ProgressState
from eda_worker.sandbox_contract import EXECUTION_GUIDANCE
from eda_worker.web_artifacts.builder import build_web_execution

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
    *,
    progress: ProgressReporter | None = None,
) -> list[FunctionTool]:
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
        inspect_artifact,
        execute_in_sandbox,
        build_web_artifact,
        validate_artifact,
        publish_artifact,
    ]


create_core_tools = create_capability_tools


def _bounded_progress_detail(value: str) -> str:
    return value if len(value) <= 240 else f"{value[:237]}..."
