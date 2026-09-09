from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from eda_worker.fabric.ontology.contracts import OntologyQueryPurpose
from eda_worker.fabric.ontology.provenance import FabricOntologyQueryDocument


def document() -> FabricOntologyQueryDocument:
    return FabricOntologyQueryDocument(
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
        session_id="ses_01HZZZZZZZZZZZZZZZZZZZZZZZ",
        query_ref="fabric-query-1",
        source_alias="lamna-healthcare",
        workspace_id=UUID("44444444-4444-4444-4444-444444444444"),
        ontology_id=UUID("55555555-5555-5555-5555-555555555555"),
        purpose=OntologyQueryPurpose.AGGREGATE,
        question="How many patients are currently admitted?",
        provider_contract_digest="a" * 64,
        entity_schema_digest="b" * 64,
        result_artifact_ref="artifact_fabric_result",
        result_sha256="c" * 64,
        result_shape="object",
        started_at=datetime(2026, 7, 25, tzinfo=UTC),
        completed_at=datetime(2026, 7, 25, tzinfo=UTC),
    )


def test_public_provenance_omits_owner_target_and_question() -> None:
    public = document().public_record()
    serialized = public.model_dump(mode="json", by_alias=True)

    assert serialized["provider"] == "ontology"
    assert serialized["sourceAlias"] == "lamna-healthcare"
    assert "workspaceId" not in serialized
    assert "ontologyId" not in serialized
    assert "ownerObjectId" not in serialized
    assert "tenantId" not in serialized
    assert "question" not in serialized


def test_public_provenance_keeps_hashes_and_artifact_reference() -> None:
    public = document().public_record()

    assert public.entity_schema_digest == "b" * 64
    assert public.result_sha256 == "c" * 64
    assert public.result_artifact_ref == "artifact_fabric_result"
