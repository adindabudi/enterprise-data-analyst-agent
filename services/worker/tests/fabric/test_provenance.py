from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from eda_contracts import ArtifactKind, ArtifactRef
from eda_contracts.tasks import TaskStatus
from eda_runtime_state.models import TaskRecord
from eda_worker.fabric.config import SemanticModelTarget
from eda_worker.fabric.contracts import FabricQueryOperation, FabricQueryPurpose
from eda_worker.fabric.planner import FabricPlannerResult
from eda_worker.fabric.provenance import (
    FabricQueryDocument,
    FabricQueryRecorder,
    InMemoryFabricQueryRepository,
)


class ArtifactStore:
    def __init__(self) -> None:
        self.payloads: list[bytes] = []

    async def persist_bytes(
        self,
        task_id: str,
        kind: ArtifactKind,
        display_name: str,
        content: bytes,
    ) -> ArtifactRef:
        assert task_id == "task_fabric_provenance_1234"
        assert kind is ArtifactKind.DATA
        assert display_name == "fabric-query-result.json"
        self.payloads.append(content)
        return ArtifactRef(
            artifact_id="artifact-fabric-result-1234",
            version=1,
            kind=ArtifactKind.DATA,
            sha256=__import__("hashlib").sha256(content).hexdigest(),
        )


def task() -> TaskRecord:
    now = datetime.now(UTC)
    return TaskRecord(
        id="task_fabric_provenance_1234",
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
        session_id="ses_1234567890abcdef",
        status=TaskStatus.ANALYZING,
        checkpoint_sequence=3,
        command_sequence=0,
        applied_command_sequence=0,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(days=1),
    )


def operation() -> FabricQueryOperation:
    return FabricQueryOperation(
        semantic_model="sales",
        purpose=FabricQueryPurpose.AGGREGATE,
        question="  What   is total revenue?  ",
    )


def planner_result() -> FabricPlannerResult:
    return FabricPlannerResult(
        summary="Revenue is 42.",
        provider_calls=("GetSemanticModelSchema", "ExecuteQuery"),
        execute_attempts=1,
        provider_schema_sha256="a" * 64,
        dax_queries=('EVALUATE ROW("Revenue", [Revenue])',),
        result={"rows": [{"Revenue": 42}], "rowCount": 1},
        result_shape="tabular",
        row_count=1,
    )


@pytest.mark.asyncio
async def test_records_owner_scoped_evidence_and_maps_an_allowlisted_public_record() -> None:
    artifacts = ArtifactStore()
    repository = InMemoryFabricQueryRepository()
    recorder = FabricQueryRecorder(artifact_store=artifacts, repository=repository)
    started_at = datetime.now(UTC)

    document = await recorder.record(
        task=task(),
        query_ref="fabric-query-1234567890abcdef",
        operation=operation(),
        target=SemanticModelTarget(
            modelId=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
            description="Curated sales measures.",
        ),
        planned=planner_result(),
        started_at=started_at,
        completed_at=started_at + timedelta(seconds=2),
    )

    persisted = await repository.get(task(), document.query_ref)
    assert persisted == document
    assert document.tenant_id == task().tenant_id
    assert document.owner_object_id == task().owner_object_id
    assert document.session_id == task().session_id
    assert document.question == "What is total revenue?"
    assert document.generated_dax == ('EVALUATE ROW("Revenue", [Revenue])',)
    assert document.row_count == 1
    assert document.result_shape == "tabular"
    assert document.result_sha256 == document.result_artifact.sha256
    assert json.loads(artifacts.payloads[0]) == {"rowCount": 1, "rows": [{"Revenue": 42}]}

    public = document.public_record()
    public_keys = set(public.model_dump(mode="json", by_alias=True))
    assert not public_keys & {"tenantId", "ownerObjectId", "sessionId", "taskId"}
    assert public.query_id == document.query_ref
    assert public.result_sha256 == document.result_sha256
    assert public.reconciliation_status == "not_required"


def test_internal_and_public_models_reject_credential_and_topology_fields() -> None:
    internal_properties = set(FabricQueryDocument.model_json_schema(by_alias=True)["properties"])
    forbidden = {
        "fabricTenantId",
        "accountId",
        "token",
        "cache",
        "ciphertext",
        "endpoint",
        "headers",
        "authUrl",
        "rawMcpMessage",
        "reasoning",
        "conversationId",
    }
    assert not internal_properties & forbidden

    base = {
        "tenantId": str(task().tenant_id),
        "ownerObjectId": str(task().owner_object_id),
        "sessionId": task().session_id,
        "taskId": task().id,
        "queryRef": "fabric-query-1234567890abcdef",
        "sourceAlias": "sales",
        "purpose": "aggregate",
        "question": "Total revenue?",
        "semanticModelId": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "providerSchemaSha256": "a" * 64,
        "generatedDax": ["EVALUATE ROW(1)"],
        "attempts": 1,
        "resultArtifact": {
            "artifactId": "artifact-fabric-result-1234",
            "version": 1,
            "kind": "data",
            "sha256": "b" * 64,
        },
        "resultSha256": "b" * 64,
        "resultShape": "tabular",
        "rowCount": 1,
        "reconciliationStatus": "not_required",
        "startedAt": datetime.now(UTC).isoformat(),
        "completedAt": datetime.now(UTC).isoformat(),
    }
    with pytest.raises(ValueError):
        FabricQueryDocument.model_validate({**base, "token": "forbidden"})
