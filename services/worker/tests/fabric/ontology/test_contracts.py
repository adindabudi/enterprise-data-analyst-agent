from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import pytest
from eda_worker.fabric.contracts import FabricPrincipal, FabricQueryResult, FabricSourceGuide
from eda_worker.fabric.ontology.contracts import FabricOntologyQueryOperation, OntologyQueryPurpose
from eda_worker.tools.capabilities import create_capability_tools
from eda_worker.tools.contracts import CapabilityResult
from pydantic import BaseModel, ValidationError


class CoreGateway:
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


class OntologyGateway:
    def __init__(self, status: str = "ok") -> None:
        self.status = status

    async def query(
        self,
        task_id: str,
        invocation_id: str,
        principal: FabricPrincipal,
        operation: BaseModel,
    ) -> FabricQueryResult:
        del task_id, invocation_id, principal, operation
        return FabricQueryResult(status=self.status, summary="bounded")  # type: ignore[arg-type]


@dataclass
class InvocationContext:
    kwargs: dict[str, object]
    metadata: dict[str, object]


def ontology_tools(gateway: OntologyGateway):
    return create_capability_tools(
        CoreGateway(),
        fabric_gateway=gateway,
        source_guides=(
            FabricSourceGuide(
                alias="lamna-healthcare",
                description="Healthcare operations ontology",
                vocabulary=("patients", "admissions", "telemetry"),
            ),
        ),
        fabric_operation_model=FabricOntologyQueryOperation,
    )


def test_model_facing_operation_exposes_only_alias_purpose_and_question() -> None:
    operation = FabricOntologyQueryOperation(
        ontology="lamna-healthcare",
        purpose=OntologyQueryPurpose.AGGREGATE,
        question="How many patients are currently admitted?",
    )

    assert set(operation.model_json_schema()["properties"]) == {"ontology", "purpose", "question"}


@pytest.mark.parametrize(
    "forbidden",
    [
        "semantic_model",
        "workspace",
        "ontology_id",
        "item_id",
        "url",
        "query",
        "dax",
        "token",
        "tenant",
        "tool",
        "naturalLanguageResponse",
    ],
)
def test_model_facing_operation_rejects_provider_internals(forbidden: str) -> None:
    with pytest.raises(ValidationError):
        FabricOntologyQueryOperation.model_validate(
            {
                "ontology": "lamna-healthcare",
                "purpose": "aggregate",
                "question": "How many patients are currently admitted?",
                forbidden: "untrusted",
            }
        )


def test_ready_ontology_exposes_only_one_fabric_capability_with_safe_vocabulary() -> None:
    tools = ontology_tools(OntologyGateway())

    assert [tool.name for tool in tools] == [
        "query_fabric",
        "inspect_artifact",
        "execute_in_sandbox",
        "build_web_artifact",
        "validate_artifact",
        "publish_artifact",
    ]
    assert set(tools[0].input_model.model_json_schema()["properties"]) == {"ontology", "purpose", "question"}
    assert "lamna-healthcare" in tools[0].description
    assert "patients, admissions, telemetry" not in tools[0].description
    for term in ("admissions", "patients", "telemetry"):
        assert term in tools[0].description
    for forbidden in ("search_ontology", "list_ontology_entity_types", "naturalLanguageResponse", "https://"):
        assert forbidden not in tools[0].description


@pytest.mark.asyncio
async def test_ontology_mode_rejects_semantic_model_correctable_error_status() -> None:
    tool = ontology_tools(OntologyGateway(status="correctable_error"))[0]
    context = InvocationContext(
        kwargs={
            "task_id": "task_12345678",
            "principal": FabricPrincipal(
                tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
                owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
                audience=UUID("33333333-3333-3333-3333-333333333333"),
            ),
        },
        metadata={"call_id": "call_12345678"},
    )

    with pytest.raises(ValueError, match="ontology result status"):
        await tool.func(
            context,
            ontology="lamna-healthcare",
            purpose="aggregate",
            question="How many patients are admitted?",
        )
