from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

import pytest
from agent_framework import ChatResponse, Content, Message
from eda_worker.fabric.contracts import FabricQueryOperation, FabricQueryPurpose
from eda_worker.fabric.mcp_client import FabricMcpToolError, JsonValue
from eda_worker.fabric.planner import FabricAnalystPlanner

MODEL_ID = UUID("11111111-1111-1111-1111-111111111111")


def function_call(call_id: str, name: str, arguments: dict[str, object]) -> ChatResponse[Any]:
    return ChatResponse(
        messages=Message(
            role="assistant",
            contents=[Content.from_function_call(call_id, name, arguments=arguments)],
        ),
        conversation_id=None,
        finish_reason="tool_calls",
    )


def final_response(text: str) -> ChatResponse[Any]:
    return ChatResponse(
        messages=Message(role="assistant", contents=[Content.from_text(text)]),
        conversation_id=None,
        finish_reason="stop",
    )


class RecordingPlanningClient:
    def __init__(self, responses: list[ChatResponse[Any]]) -> None:
        self.responses = responses
        self.calls: list[tuple[list[Message], dict[str, Any]]] = []

    async def get_response(
        self,
        messages: Sequence[Message],
        *,
        stream: bool,
        options: Mapping[str, Any],
    ) -> ChatResponse[Any]:
        assert stream is False
        self.calls.append((list(messages), dict(options)))
        return self.responses.pop(0)


class RecordingMcpClient:
    def __init__(self, execute_errors: list[FabricMcpToolError] | None = None) -> None:
        self.calls: list[tuple[str, UUID, dict[str, object]]] = []
        self.execute_errors = execute_errors or []

    async def call(
        self,
        name: str,
        *,
        semantic_model_id: UUID,
        arguments: Mapping[str, object],
    ) -> JsonValue:
        self.calls.append((name, semantic_model_id, dict(arguments)))
        if name == "ExecuteQuery" and self.execute_errors:
            raise self.execute_errors.pop(0)
        if name == "GetSemanticModelSchema":
            return {"tables": [{"name": "Sales"}], "measures": [{"name": "Revenue"}]}
        return {"rows": [{"Revenue": 42}], "rowCount": 1}


def operation() -> FabricQueryOperation:
    return FabricQueryOperation(
        semantic_model="sales",
        purpose=FabricQueryPurpose.AGGREGATE,
        question="What is total revenue?",
    )


@pytest.mark.asyncio
async def test_schema_first_manual_loop_uses_three_local_tools_and_store_false() -> None:
    planning_client = RecordingPlanningClient(
        [
            function_call("call-schema", "GetSemanticModelSchema", {}),
            function_call(
                "call-query",
                "ExecuteQuery",
                {"daxQueries": ['EVALUATE ROW("Revenue", [Revenue])']},
            ),
            final_response("Revenue is 42 for the requested filter context."),
        ]
    )
    mcp_client = RecordingMcpClient()
    planner = FabricAnalystPlanner(
        planning_client,
        mcp_client,
        model_options={"reasoning": {"mode": "standard", "effort": "medium"}, "max_tokens": 8_000},
    )

    result = await planner.run(operation(), semantic_model_id=MODEL_ID)

    assert result.summary == "Revenue is 42 for the requested filter context."
    assert result.provider_calls == ("GetSemanticModelSchema", "ExecuteQuery")
    assert [name for name, _, _ in mcp_client.calls] == ["GetSemanticModelSchema", "ExecuteQuery"]
    for _, options in planning_client.calls:
        assert options["store"] is False
        assert options["tool_choice"] == {"mode": "auto"}
        assert [tool.name for tool in options["tools"]] == [
            "GetSemanticModelSchema",
            "ValueSearch",
            "ExecuteQuery",
        ]


@pytest.mark.asyncio
async def test_first_provider_action_must_be_schema() -> None:
    planning_client = RecordingPlanningClient(
        [function_call("call-query", "ExecuteQuery", {"daxQueries": ["EVALUATE ROW(1)"]})]
    )
    planner = FabricAnalystPlanner(planning_client, RecordingMcpClient(), model_options={})

    with pytest.raises(ValueError, match="schema first"):
        await planner.run(operation(), semantic_model_id=MODEL_ID)


@pytest.mark.asyncio
async def test_allows_one_dax_correction_only_after_structured_invalid_dax() -> None:
    planning_client = RecordingPlanningClient(
        [
            function_call("call-schema", "GetSemanticModelSchema", {}),
            function_call("call-query-1", "ExecuteQuery", {"daxQueries": ["EVALUATE BAD"]}),
            function_call("call-query-2", "ExecuteQuery", {"daxQueries": ["EVALUATE ROW(1)"]}),
            final_response("Corrected result is 42."),
        ]
    )
    mcp_client = RecordingMcpClient([FabricMcpToolError("invalid_dax", "DAX syntax is invalid")])
    planner = FabricAnalystPlanner(planning_client, mcp_client, model_options={})

    result = await planner.run(operation(), semantic_model_id=MODEL_ID)

    assert result.provider_calls == ("GetSemanticModelSchema", "ExecuteQuery", "ExecuteQuery")
    assert result.execute_attempts == 2


@pytest.mark.asyncio
async def test_rejects_a_third_execute_or_more_than_six_turns() -> None:
    responses = [function_call("schema", "GetSemanticModelSchema", {})]
    responses.extend(
        function_call(f"query-{index}", "ExecuteQuery", {"daxQueries": ["EVALUATE BAD"]}) for index in range(1, 4)
    )
    mcp_client = RecordingMcpClient(
        [
            FabricMcpToolError("invalid_dax", "invalid"),
            FabricMcpToolError("invalid_dax", "invalid"),
        ]
    )
    planner = FabricAnalystPlanner(RecordingPlanningClient(responses), mcp_client, model_options={})

    with pytest.raises(ValueError, match="correction limit"):
        await planner.run(operation(), semantic_model_id=MODEL_ID)

    endless = RecordingPlanningClient(
        [function_call(f"schema-{index}", "GetSemanticModelSchema", {}) for index in range(6)]
    )
    with pytest.raises(ValueError, match="turn limit"):
        await FabricAnalystPlanner(endless, RecordingMcpClient(), model_options={}).run(
            FabricQueryOperation(
                semantic_model="sales",
                purpose=FabricQueryPurpose.SCHEMA,
                question="Describe the model.",
            ),
            semantic_model_id=MODEL_ID,
        )
