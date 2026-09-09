from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import ClassVar
from uuid import UUID

import pytest
from eda_contracts.controls import CommandKind
from eda_contracts.tasks import TaskStatus
from eda_runtime_state.models import TaskRecord
from eda_runtime_state.tasks import InMemoryRuntimeStateRepository
from eda_worker.fabric.contracts import FabricPrincipal
from eda_worker.fabric.ontology.capability import FabricOntologyCapabilityGateway
from eda_worker.fabric.ontology.config import OntologyTarget
from eda_worker.fabric.ontology.contracts import FabricOntologyQueryOperation, OntologyQueryPurpose
from eda_worker.fabric.ontology.gateway import OntologyGatewayResult
from eda_worker.sandbox.gateway import InMemoryArtifactGatewayStore


class ProviderGateway:
    def __init__(self) -> None:
        self.calls = 0

    async def query(self, **kwargs: object) -> OntologyGatewayResult:
        self.calls += 1
        assert kwargs["provider_contract_digest"] == "a" * 64
        return OntologyGatewayResult(
            value={"rows": [{"count": 5}]},
            grounding={"entities": []},
            grounding_digest="b" * 64,
        )

    def clear_task(self, owner_partition_key: str, task_id: str) -> None:
        del owner_partition_key, task_id


class Evidence:
    def __init__(self) -> None:
        self.documents: list[object] = []

    async def put(self, document: object) -> object:
        self.documents.append(document)
        return document


def principal() -> FabricPrincipal:
    return FabricPrincipal(
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
        audience=UUID("33333333-3333-3333-3333-333333333333"),
    )


def task() -> TaskRecord:
    now = datetime.now(UTC)
    owner = principal()
    return TaskRecord(
        id="task_ontology_12345678",
        tenant_id=owner.tenant_id,
        owner_object_id=owner.owner_object_id,
        session_id="ses_ontology_12345678",
        status=TaskStatus.ANALYZING,
        checkpoint_sequence=2,
        command_sequence=0,
        applied_command_sequence=0,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(days=1),
    )


def operation() -> FabricOntologyQueryOperation:
    return FabricOntologyQueryOperation(
        ontology="lamna-healthcare",
        purpose=OntologyQueryPurpose.AGGREGATE,
        question="How many patients are admitted?",
    )


async def stack(
    provider: ProviderGateway | None = None,
) -> tuple[FabricOntologyCapabilityGateway, InMemoryRuntimeStateRepository, ProviderGateway, Evidence]:
    runtime = InMemoryRuntimeStateRepository()
    current = task()
    await runtime.create_task(current, "ontology-task")
    provider = provider or ProviderGateway()
    evidence = Evidence()
    gateway = FabricOntologyCapabilityGateway(
        catalog={
            "lamna-healthcare": OntologyTarget(
                workspaceId=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
                ontologyId=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
                description="Synthetic hospital operations.",
            )
        },
        gateway_factory=lambda _: provider,  # type: ignore[arg-type]
        runtime=runtime,
        artifacts=InMemoryArtifactGatewayStore(),
        evidence=evidence,  # type: ignore[arg-type]
        provider_contract_digest="a" * 64,
    )
    return gateway, runtime, provider, evidence


@pytest.mark.asyncio
async def test_capability_persists_structured_evidence_and_replays_operation() -> None:
    gateway, _, provider, evidence = await stack()

    first = await gateway.query(task().id, "invoke_ontology_1234", principal(), operation())
    second = await gateway.query(task().id, "invoke_ontology_1234", principal(), operation())

    assert first == second
    assert first.status == "ok"
    assert len(first.artifact_refs) == 1
    assert provider.calls == 1
    assert len(evidence.documents) == 1
    assert "workspace_id" not in first.model_dump(mode="json")


class SchemaProviderGateway(ProviderGateway):
    grounding: ClassVar[dict[str, object]] = {
        "entities": [
            {
                "name": "patients",
                "description": "Admitted people.",
                "synonyms": ["residents"],
                "keyProperties": ["PatientId"],
                "properties": [{"name": "PatientId", "valueType": "BigInt"}],
                "timeSeriesProperties": [],
            }
        ]
    }

    async def query(self, **kwargs: object) -> OntologyGatewayResult:
        self.calls += 1
        return OntologyGatewayResult(value=self.grounding, grounding=self.grounding, grounding_digest="b" * 64)


@pytest.mark.asyncio
async def test_a_schema_call_answers_with_the_schema_not_only_an_artifact_reference() -> None:
    # The reply carried one constant sentence, so the values a filter may use cost a second tool call.
    gateway, _, _, _ = await stack(SchemaProviderGateway())

    result = await gateway.query(
        task().id,
        "invoke_ontology_9012",
        principal(),
        FabricOntologyQueryOperation(
            ontology="lamna-healthcare",
            purpose=OntologyQueryPurpose.SCHEMA,
            question="What does this source hold?",
        ),
    )

    assert "patients (also: residents): PatientId BigInt -- Admitted people." in result.summary
    assert len(result.artifact_refs) == 1


@pytest.mark.asyncio
async def test_capability_rejects_wrong_owner_and_honors_cancellation() -> None:
    gateway, runtime, provider, _ = await stack()
    wrong_owner = principal().model_copy(update={"owner_object_id": UUID("44444444-4444-4444-4444-444444444444")})
    with pytest.raises(ValueError, match="owner"):
        await gateway.query(task().id, "invoke_ontology_1234", wrong_owner, operation())

    current = task()
    await runtime.append_command(current.partition(), current.id, CommandKind.CANCEL, None, "cancel-ontology")
    cancelled = await gateway.query(task().id, "invoke_ontology_5678", principal(), operation())

    assert cancelled.status == "cancelled"
    assert provider.calls == 0
