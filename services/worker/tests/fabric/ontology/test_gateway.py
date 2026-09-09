from __future__ import annotations

from uuid import UUID

import pytest
from eda_worker.fabric.ontology.config import OntologyTarget
from eda_worker.fabric.ontology.contracts import FabricOntologyQueryOperation, OntologyQueryPurpose
from eda_worker.fabric.ontology.gateway import FabricOntologyGateway


def target() -> OntologyTarget:
    return OntologyTarget(
        workspace_id=UUID("44444444-4444-4444-4444-444444444444"),
        ontology_id=UUID("55555555-5555-5555-5555-555555555555"),
        description="Synthetic hospital operations.",
    )


def schema() -> dict[str, object]:
    return {
        "values": [
            {
                "id": "entity-id",
                "name": "patients",
                "entityIdParts": ["key-id"],
                "properties": [{"id": "key-id", "name": "PatientId", "valueType": "BigInt"}],
                "timeseriesProperties": [],
                "mappings": [{"workspaceId": "must-not-leak"}],
            }
        ]
    }


class FakeMcpClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def inspect(self) -> dict[str, object]:
        self.calls.append("inspect")
        return schema()

    async def search(self, *, question: str) -> dict[str, object]:
        self.calls.append("search")
        return {"rows": [{"count": 5}], "question": question}

    async def inspect_and_search(self, *, question: str) -> tuple[dict[str, object], dict[str, object]]:
        self.calls.append("inspect_and_search")
        return schema(), {"rows": [{"count": 5}], "question": question}


def operation(purpose: OntologyQueryPurpose = OntologyQueryPurpose.AGGREGATE) -> FabricOntologyQueryOperation:
    return FabricOntologyQueryOperation(
        ontology="lamna-healthcare",
        purpose=purpose,
        question="How many patients are currently admitted?",
    )


@pytest.mark.asyncio
async def test_cold_then_warm_query_reuses_safe_task_grounding() -> None:
    client = FakeMcpClient()
    gateway = FabricOntologyGateway(
        catalog={"lamna-healthcare": target()},
        client_factory=lambda _: client,
    )

    cold = await gateway.query(
        owner_partition_key="owner-a",
        task_id="task_1",
        provider_contract_digest="a" * 64,
        operation=operation(),
    )
    warm = await gateway.query(
        owner_partition_key="owner-a",
        task_id="task_1",
        provider_contract_digest="a" * 64,
        operation=operation(),
    )

    assert client.calls == ["inspect_and_search", "search"]
    assert cold.value == warm.value == {"rows": [{"count": 5}], "question": operation().question}
    assert "must-not-leak" not in str(cold.grounding)


@pytest.mark.asyncio
async def test_schema_operation_inspects_without_searching() -> None:
    client = FakeMcpClient()
    gateway = FabricOntologyGateway(
        catalog={"lamna-healthcare": target()},
        client_factory=lambda _: client,
    )

    result = await gateway.query(
        owner_partition_key="owner-a",
        task_id="task_1",
        provider_contract_digest="a" * 64,
        operation=operation(OntologyQueryPurpose.SCHEMA),
    )

    assert client.calls == ["inspect"]
    assert result.value == result.grounding


@pytest.mark.asyncio
async def test_unknown_alias_fails_before_client_creation() -> None:
    gateway = FabricOntologyGateway(catalog={}, client_factory=lambda _: pytest.fail("client must not be created"))

    with pytest.raises(ValueError, match="configured ontology"):
        await gateway.query(
            owner_partition_key="owner-a",
            task_id="task_1",
            provider_contract_digest="a" * 64,
            operation=operation(),
        )


@pytest.mark.asyncio
async def test_grounding_cache_isolated_by_contract_and_cleared_per_task() -> None:
    client = FakeMcpClient()
    gateway = FabricOntologyGateway(
        catalog={"lamna-healthcare": target()},
        client_factory=lambda _: client,
    )
    request = operation()

    await gateway.query(
        owner_partition_key="owner-a",
        task_id="task_1",
        provider_contract_digest="a" * 64,
        operation=request,
    )
    await gateway.query(
        owner_partition_key="owner-a",
        task_id="task_1",
        provider_contract_digest="b" * 64,
        operation=request,
    )
    gateway.clear_task("owner-a", "task_1")
    await gateway.query(
        owner_partition_key="owner-a",
        task_id="task_1",
        provider_contract_digest="b" * 64,
        operation=request,
    )

    assert client.calls == ["inspect_and_search", "inspect_and_search", "inspect_and_search"]


@pytest.mark.asyncio
async def test_cold_query_rejects_grounding_drift_even_when_search_returned_data() -> None:
    client = FakeMcpClient()
    gateway = FabricOntologyGateway(
        catalog={"lamna-healthcare": target()},
        client_factory=lambda _: client,
        expected_grounding_digests={"lamna-healthcare": "f" * 64},
    )

    with pytest.raises(ValueError, match="grounding drifted"):
        await gateway.query(
            owner_partition_key="owner-a",
            task_id="task_1",
            provider_contract_digest="a" * 64,
            operation=operation(),
        )

    assert client.calls == ["inspect_and_search"]
