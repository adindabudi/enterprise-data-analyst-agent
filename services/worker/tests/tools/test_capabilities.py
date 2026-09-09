from __future__ import annotations

from dataclasses import dataclass

import pytest
from agent_framework import Content
from agent_framework._tools import _auto_invoke_function, normalize_function_invocation_configuration
from eda_contracts import ArtifactKind, ArtifactRef
from eda_worker.tools.capabilities import create_capability_tools
from eda_worker.tools.contracts import (
    BuildWebArtifactOperation,
    CapabilityResult,
    ExecuteSandboxOperation,
    SandboxRuntime,
)


@dataclass
class InvocationContext:
    kwargs: dict[str, str]


class FakeGateway:
    def __init__(self) -> None:
        self.cancelled = False
        self.executions: list[ExecuteSandboxOperation] = []

    async def is_cancelled(self, task_id: str) -> bool:
        return self.cancelled

    async def inspect(self, task_id: str, operation) -> CapabilityResult:
        del task_id, operation
        return CapabilityResult(status="ok", summary="inspected")

    async def execute(self, task_id: str, operation: ExecuteSandboxOperation) -> CapabilityResult:
        del task_id
        self.executions.append(operation)
        return CapabilityResult(status="ok", summary="executed")

    async def validate(self, task_id: str, operation) -> CapabilityResult:
        del task_id, operation
        return CapabilityResult(status="ok", summary="validated")

    async def publish(self, task_id: str, operation) -> CapabilityResult:
        del task_id, operation
        return CapabilityResult(status="ok", summary="published")


def valid_execute_operation() -> ExecuteSandboxOperation:
    return ExecuteSandboxOperation(
        runtime=SandboxRuntime.PYTHON,
        source="print('synthetic')",
        input_artifacts=(
            ArtifactRef(
                artifact_id="input-12345678",
                version=1,
                kind=ArtifactKind.INPUT,
                sha256="a" * 64,
            ),
        ),
    )


def tool_by_name(tools, name: str):
    return next(tool for tool in tools if tool.name == name)


def test_core_tool_names_are_exact() -> None:
    tools = create_capability_tools(FakeGateway())

    assert [tool.name for tool in tools] == [
        "inspect_artifact",
        "execute_in_sandbox",
        "build_web_artifact",
        "validate_artifact",
        "publish_artifact",
    ]
    assert all(tool.approval_mode == "never_require" for tool in tools)


def test_model_cannot_supply_paths_credentials_or_executables() -> None:
    schemas = {tool.name: str(tool.parameters()) for tool in create_capability_tools(FakeGateway())}
    for schema in schemas.values():
        assert "storage_path" not in schema
        assert "credential" not in schema
        assert "authorization" not in schema
        assert "executable" not in schema


@pytest.mark.asyncio
async def test_expensive_tool_checks_cancellation_first() -> None:
    gateway = FakeGateway()
    gateway.cancelled = True
    tool = tool_by_name(create_capability_tools(gateway), "execute_in_sandbox")
    result = await tool.func(InvocationContext({"task_id": "task_12345678"}), **valid_execute_operation().model_dump())

    assert result["status"] == "cancelled"
    assert gateway.executions == []


@pytest.mark.asyncio
async def test_framework_auto_invocation_accepts_sandbox_input_artifact_array() -> None:
    gateway = FakeGateway()
    tool = tool_by_name(create_capability_tools(gateway), "execute_in_sandbox")
    operation = valid_execute_operation()
    function_call = Content.from_function_call(
        call_id="call_12345678",
        name=tool.name,
        arguments=operation.model_dump(mode="json"),
    )

    result = await _auto_invoke_function(
        function_call,
        {"task_id": "task_12345678"},
        config=normalize_function_invocation_configuration(None),
        tool_map={tool.name: tool},
    )

    assert result.exception is None
    assert gateway.executions == [operation]


@pytest.mark.asyncio
async def test_script_execution_reports_start_and_finish() -> None:
    gateway = FakeGateway()
    progress: list[tuple[str, str, str | None, str]] = []

    async def report(task_id: str, milestone: str, detail: str | None, state: str) -> None:
        progress.append((task_id, milestone, detail, state))

    tool = tool_by_name(create_capability_tools(gateway, progress=report), "execute_in_sandbox")
    result = await tool.func(
        InvocationContext({"task_id": "task_12345678"}),
        **valid_execute_operation().model_dump(),
    )

    assert result["status"] == "ok"
    assert progress == [
        ("task_12345678", "Running analysis script", "Python sandbox execution started.", "running"),
        ("task_12345678", "Analysis script finished", "executed", "completed"),
    ]


@pytest.mark.asyncio
async def test_web_artifact_tool_runs_only_the_first_party_bounded_build() -> None:
    gateway = FakeGateway()
    tool = tool_by_name(create_capability_tools(gateway), "build_web_artifact")

    result = await tool.func(
        InvocationContext({"task_id": "task_12345678"}),
        **BuildWebArtifactOperation(
            title="Executive dashboard",
            display_name="dashboard.html",
            app_source="export default function App(){return <main>Revenue</main>}",
            styles="main { display: grid; }",
        ).model_dump(),
    )

    assert result["status"] == "ok"
    assert len(gateway.executions) == 1
    execution = gateway.executions[0]
    assert execution.runtime is SandboxRuntime.PYTHON
    assert "eda_sandbox.cli" in execution.source
    assert "bundle-web" in execution.source
