from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import pytest
from eda_worker.fabric.contracts import (
    FabricPrincipal,
    FabricQueryOperation,
    FabricQueryPurpose,
    FabricQueryResult,
    FabricSourceGuide,
    build_query_fabric_description,
)
from eda_worker.tools.capabilities import create_capability_tools
from eda_worker.tools.contracts import CapabilityResult
from pydantic import BaseModel, ValidationError


class FakeGateway:
    async def is_cancelled(self, task_id: str) -> bool:
        del task_id
        return False

    async def inspect(self, task_id: str, operation: object) -> CapabilityResult:
        del task_id, operation
        return CapabilityResult(status="ok", summary="inspected")

    async def execute(self, task_id: str, operation: object) -> CapabilityResult:
        del task_id, operation
        return CapabilityResult(status="ok", summary="executed")

    async def validate(self, task_id: str, operation: object) -> CapabilityResult:
        del task_id, operation
        return CapabilityResult(status="ok", summary="validated")

    async def publish(self, task_id: str, operation: object) -> CapabilityResult:
        del task_id, operation
        return CapabilityResult(status="ok", summary="published")


@dataclass
class InvocationContext:
    kwargs: dict[str, object]
    metadata: dict[str, object]


class FakeFabricGateway:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, FabricPrincipal, BaseModel]] = []

    async def query(
        self,
        task_id: str,
        invocation_id: str,
        principal: FabricPrincipal,
        operation: BaseModel,
    ) -> FabricQueryResult:
        self.calls.append((task_id, invocation_id, principal, operation))
        return FabricQueryResult(status="ok", summary="Authoritative bounded result.", query_ref="fabric-query-test")


def guides() -> tuple[FabricSourceGuide, ...]:
    return (
        FabricSourceGuide(alias="sales", description="Curated sales model", vocabulary=("revenue", "region")),
        FabricSourceGuide(alias="finance", description="Finance control model", vocabulary=("quarter",)),
    )


def test_ready_fabric_adds_exactly_one_application_capability_first() -> None:
    tools = create_capability_tools(
        FakeGateway(),
        fabric_gateway=FakeFabricGateway(),
        source_guides=guides(),
        fabric_operation_model=FabricQueryOperation,
    )

    assert [tool.name for tool in tools] == [
        "query_fabric",
        "inspect_artifact",
        "execute_in_sandbox",
        "build_web_artifact",
        "validate_artifact",
        "publish_artifact",
    ]
    assert set(tools[0].input_model.model_json_schema()["properties"]) == {
        "semantic_model",
        "purpose",
        "question",
    }


@pytest.mark.parametrize(
    "forbidden",
    ["workspace", "artifact_id", "model_id", "url", "dax", "token", "headers", "tenant", "retry"],
)
def test_semantic_model_operation_rejects_provider_internals(forbidden: str) -> None:
    with pytest.raises(ValidationError):
        FabricQueryOperation.model_validate(
            {
                "semantic_model": "sales",
                "purpose": "aggregate",
                "question": "What is total revenue?",
                forbidden: "untrusted",
            }
        )


@pytest.mark.asyncio
async def test_fabric_tool_uses_only_trusted_context_for_scope() -> None:
    gateway = FakeFabricGateway()
    principal = FabricPrincipal(
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
        audience=UUID("33333333-3333-3333-3333-333333333333"),
    )
    tool = create_capability_tools(
        FakeGateway(),
        fabric_gateway=gateway,
        source_guides=guides(),
        fabric_operation_model=FabricQueryOperation,
    )[0]

    result = await tool.func(
        InvocationContext(
            kwargs={
                "task_id": "task_12345678",
                "principal": principal,
            },
            metadata={"call_id": "call_12345678"},
        ),
        semantic_model="sales",
        purpose=FabricQueryPurpose.AGGREGATE,
        question="What is total revenue?",
    )

    assert result["status"] == "ok"
    assert gateway.calls == [
        (
            "task_12345678",
            "call_12345678",
            principal,
            FabricQueryOperation(
                semantic_model="sales",
                purpose=FabricQueryPurpose.AGGREGATE,
                question="What is total revenue?",
            ),
        )
    ]


def test_description_is_deterministic_bounded_and_contains_safe_routing_examples() -> None:
    first = build_query_fabric_description(guides())
    second = build_query_fabric_description(tuple(reversed(guides())))

    assert first == second
    assert len(first.encode("utf-8")) <= 16_384
    for expected in (
        "sales",
        "Curated sales model",
        "schema",
        "entities",
        "properties",
        "relationships",
        "metrics",
        "aggregates",
        "control totals",
        "time-series values",
        "Example success",
        "Example ambiguity",
        "authorization_error",
        "do not supply a value from model knowledge",
    ):
        assert expected in first


@pytest.mark.parametrize(
    "unsafe",
    [
        "https://fabric.example/query",
        "11111111-1111-1111-1111-111111111111",
        "Use execute_dax_query",
        "Bearer token",
        "tenant secret",
    ],
)
def test_description_rejects_provider_topology_and_credentials(unsafe: str) -> None:
    with pytest.raises(ValueError):
        build_query_fabric_description((FabricSourceGuide(alias="sales", description=unsafe),))


def test_description_rejects_more_than_sixteen_kib() -> None:
    large = tuple(
        FabricSourceGuide(alias=f"source-{index:03d}", description="x" * 240, vocabulary=("value" * 20,))
        for index in range(60)
    )

    with pytest.raises(ValueError, match="16 KiB"):
        build_query_fabric_description(large)
