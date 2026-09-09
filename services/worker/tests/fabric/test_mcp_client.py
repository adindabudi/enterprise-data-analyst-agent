from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest
from eda_worker.fabric.config import FABRIC_IQ_MCP_URL, FABRIC_IQ_VARIANT
from eda_worker.fabric.mcp_client import (
    MAX_MCP_RESULT_BYTES,
    FabricMcpClient,
    validate_fabric_tools,
)

FIXTURES = Path(__file__).parent / "fixtures"
MODEL_ID = UUID("11111111-1111-1111-1111-111111111111")


def tool_fixture(name: str = "tools-list-six.json") -> list[dict[str, object]]:
    payload = cast(dict[str, object], json.loads((FIXTURES / name).read_text(encoding="utf-8")))
    return cast(list[dict[str, object]], payload["tools"])


class FakeSession:
    def __init__(self, *, result: object | None = None) -> None:
        self.calls: list[str] = []
        self.arguments: dict[str, object] | None = None
        self.result = result or {"structuredContent": {"rows": [{"total": 42}]}}

    async def initialize(self) -> None:
        self.calls.append("initialize")

    async def list_tools(self) -> list[dict[str, object]]:
        self.calls.append("tools/list")
        return tool_fixture()

    async def call_tool(self, name: str, arguments: dict[str, object]) -> object:
        self.calls.append(name)
        self.arguments = arguments
        return self.result


def test_accepts_only_the_pinned_six_tool_contract() -> None:
    validated = validate_fabric_tools(tool_fixture(), tool_fixture())

    assert tuple(validated) == (
        "DiscoverArtifacts",
        "ExecuteQuery",
        "GetReportMetadata",
        "GetSemanticModelSchema",
        "ResolveReportIdFromUrl",
        "ValueSearch",
    )
    with pytest.raises(ValueError, match="six-tool contract"):
        validate_fabric_tools(tool_fixture("tools-list-drift.json"), tool_fixture())


@pytest.mark.asyncio
async def test_one_operation_uses_exact_transport_order_and_trusted_model_id() -> None:
    session = FakeSession()
    closed = False

    async def token_provider() -> str:
        return "test" + "-token"

    @asynccontextmanager
    async def open_session(url: str, headers: dict[str, str]):
        nonlocal closed
        assert url == FABRIC_IQ_MCP_URL
        assert headers == {
            "Authorization": "Bearer test-token",
            "X-VARIANTS": FABRIC_IQ_VARIANT,
        }
        try:
            yield session
        finally:
            closed = True

    client = FabricMcpClient(
        token_provider=token_provider,
        expected_tools=tool_fixture(),
        session_opener=open_session,
    )

    result = await client.call(
        "ExecuteQuery",
        semantic_model_id=MODEL_ID,
        arguments={"daxQueries": ['EVALUATE ROW("total", 42)']},
    )

    assert result == {"rows": [{"total": 42}]}
    assert session.calls == ["initialize", "tools/list", "ExecuteQuery"]
    assert session.arguments == {
        "artifactId": str(MODEL_ID),
        "daxQueries": ['EVALUATE ROW("total", 42)'],
    }
    assert closed is True


@pytest.mark.asyncio
async def test_runtime_rejects_nonallowlisted_tools_and_conflicting_identifiers() -> None:
    async def token_provider() -> str:
        return "test" + "-token"

    @asynccontextmanager
    async def should_not_open(url: str, headers: dict[str, str]):
        del url, headers
        raise AssertionError("transport must not open")
        yield FakeSession()

    client = FabricMcpClient(
        token_provider=token_provider,
        expected_tools=tool_fixture(),
        session_opener=should_not_open,
    )

    with pytest.raises(ValueError, match="runtime allowlist"):
        await client.call("DiscoverArtifacts", semantic_model_id=MODEL_ID, arguments={"question": "sales"})
    with pytest.raises(ValueError, match="trusted semantic model"):
        await client.call(
            "GetSemanticModelSchema",
            semantic_model_id=MODEL_ID,
            arguments={"artifactId": "22222222-2222-2222-2222-222222222222"},
        )


@pytest.mark.asyncio
async def test_oversized_results_fail_after_closing_the_session() -> None:
    session = FakeSession(result={"structuredContent": {"value": "x" * MAX_MCP_RESULT_BYTES}})
    closed = False

    async def token_provider() -> str:
        return "test" + "-token"

    @asynccontextmanager
    async def open_session(url: str, headers: dict[str, str]):
        nonlocal closed
        del url, headers
        try:
            yield session
        finally:
            closed = True

    client = FabricMcpClient(
        token_provider=token_provider,
        expected_tools=tool_fixture(),
        session_opener=open_session,
    )

    with pytest.raises(ValueError, match="2 MiB"):
        await client.call("GetSemanticModelSchema", semantic_model_id=MODEL_ID, arguments={})
    assert closed is True


@pytest.mark.asyncio
async def test_cancellation_closes_the_session() -> None:
    entered = asyncio.Event()
    closed = False

    class BlockingSession(FakeSession):
        async def call_tool(self, name: str, arguments: dict[str, object]) -> object:
            del name, arguments
            entered.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    async def token_provider() -> str:
        return "test" + "-token"

    @asynccontextmanager
    async def open_session(url: str, headers: dict[str, str]):
        nonlocal closed
        del url, headers
        try:
            yield BlockingSession()
        finally:
            closed = True

    client = FabricMcpClient(
        token_provider=token_provider,
        expected_tools=tool_fixture(),
        session_opener=open_session,
    )
    task = asyncio.create_task(client.call("GetSemanticModelSchema", semantic_model_id=MODEL_ID, arguments={}))
    await entered.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed is True
