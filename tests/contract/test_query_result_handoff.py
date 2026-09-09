"""The API writes a query result; the durable worker reads it back.

Nothing at runtime checks that these two agree. The worker resolves an artifact
by reference, parses the Cosmos record with ``extra="forbid"``, and rejects any
digest that disagrees with either the record or the reference, so a single
renamed field in either component would strand every handed-off query result
with no error until a user asked for an export.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from uuid import UUID

from eda_api.storage.query_results import _CandidateMetadata, artifact_id, blob_name
from eda_contracts import ArtifactKind, ArtifactRef
from eda_runtime_state.models import TaskRecord, TaskStatus
from eda_worker.sandbox.gateway import _ArtifactGatewayMetadata

TASK = TaskRecord(
    id="task_abcdefgh",
    tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
    owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
    session_id="ses_interactive_12345678",
    status=TaskStatus.ANALYZING,
    checkpoint_sequence=0,
    command_sequence=0,
    applied_command_sequence=0,
    created_at=datetime(2026, 7, 31, tzinfo=UTC),
    updated_at=datetime(2026, 7, 31, tzinfo=UTC),
    expires_at=datetime(2026, 8, 31, tzinfo=UTC),
)
ROWS = b'[{"dept":1,"total_rooms":24,"occupied":21}]'
DISPLAY_NAME = "fabric-query-result.json"


def _api_record() -> dict[str, object]:
    digest = hashlib.sha256(ROWS).hexdigest()
    ref = ArtifactRef(
        artifact_id=artifact_id(TASK, kind=ArtifactKind.DATA, display_name=DISPLAY_NAME, digest=digest),
        version=1,
        kind=ArtifactKind.DATA,
        sha256=digest,
    )
    metadata = _CandidateMetadata(
        id=f"artifact::{ref.artifact_id}::v{ref.version}",
        tenant_id=str(TASK.tenant_id),
        owner_object_id=str(TASK.owner_object_id),
        session_id=TASK.session_id,
        task_id=TASK.id,
        artifact_id=ref.artifact_id,
        version=ref.version,
        kind=ref.kind,
        sha256=ref.sha256,
        display_name=DISPLAY_NAME,
        blob_name=blob_name(TASK, ref),
        size_bytes=len(ROWS),
    )
    return metadata.model_dump(mode="json", by_alias=True)


def test_the_worker_parses_the_record_the_api_writes() -> None:
    parsed = _ArtifactGatewayMetadata.model_validate(_api_record())

    assert parsed.sha256 == hashlib.sha256(ROWS).hexdigest()
    assert parsed.kind is ArtifactKind.DATA
    assert parsed.task_id == TASK.id


def test_the_partition_fields_are_written_under_the_names_cosmos_partitions_on() -> None:
    body = _api_record()

    # The worker model sets populate_by_name, so parsing alone would accept snake_case and the
    # document would land in the wrong logical partition, unreadable and with no error anywhere.
    assert {"tenantId", "ownerObjectId", "sessionId"} <= set(body)
    assert body["tenantId"] == str(TASK.tenant_id)
    assert body["ownerObjectId"] == str(TASK.owner_object_id)
    assert body["sessionId"] == TASK.session_id
    assert not {key for key in body if "_" in key}


def test_a_handed_off_result_can_never_read_as_a_published_artifact() -> None:
    parsed = _ArtifactGatewayMetadata.model_validate(_api_record())

    # published_refs and the Outputs tab both select on IS_DEFINED(c.sourceVersion).
    assert parsed.source_version is None
    assert parsed.validation_status is None
    assert parsed.report_artifact_id is None


def test_both_components_derive_the_same_identifier_and_blob_path() -> None:
    from eda_worker.sandbox.gateway import CosmosBlobArtifactGatewayStore

    digest = hashlib.sha256(ROWS).hexdigest()
    theirs = CosmosBlobArtifactGatewayStore._deterministic_artifact_id(
        TASK, kind=ArtifactKind.DATA, display_name=DISPLAY_NAME, digest=digest
    )
    ref = ArtifactRef(artifact_id=theirs, version=1, kind=ArtifactKind.DATA, sha256=digest)

    assert artifact_id(TASK, kind=ArtifactKind.DATA, display_name=DISPLAY_NAME, digest=digest) == theirs
    assert blob_name(TASK, ref) == CosmosBlobArtifactGatewayStore._blob_name(TASK, ref)


def test_the_task_record_survives_the_round_trip_cosmos_performs_on_it() -> None:
    from eda_contracts.tasks import TaskStatus
    from eda_runtime_state.models import QueryResultRef, TaskRecord

    stored = QueryResultRef(
        artifact_id="artifact-" + "0" * 40,
        version=1,
        kind="data",
        sha256="b" * 64,
        display_name="query-result-1.json",
        query="MATCH (n) RETURN n",
        query_sha256="c" * 64,
        row_count=44,
        source_alias="lamna",
        executed_at=TASK.created_at,
    )
    task = TASK.model_copy(update={"status": TaskStatus.PLANNING, "query_results": (stored,)})

    # CosmosRuntimeStateRepository.create_task dumps by alias and validates the response back.
    body = task.model_dump(mode="json", by_alias=True, exclude={"etag"})
    restored = TaskRecord.model_validate(body)

    assert restored.query_results == task.query_results
    assert not {key for key in body["queryResults"][0] if "_" in key}


COSMOS_SYSTEM_FIELDS = {
    "_rid": "zHJmAI7IHzwCAQAAAAAAAA==",
    "_self": "dbs/zHJmAA==/colls/zHJmAI7IHzw=/docs/zHJmAI7IHzwCAQAAAAAAAA==/",
    "_etag": '"350092f1-0000-1800-0000-6a6c1f3d0000"',
    "_attachments": "attachments/",
    "_ts": 1785470781,
}


def test_an_artifact_record_read_back_from_cosmos_still_validates() -> None:
    from eda_worker.sandbox.gateway import _without_cosmos_fields

    stored = _api_record() | COSMOS_SYSTEM_FIELDS

    # Production failed here with exactly five validation errors, one per system field, the moment
    # the looping agent rewrote an identical script and hit the 409 read-back path.
    parsed = _ArtifactGatewayMetadata.model_validate(_without_cosmos_fields(stored))

    assert parsed.artifact_id == _api_record()["artifactId"]
    assert parsed.sha256 == hashlib.sha256(ROWS).hexdigest()


def test_stripping_system_fields_leaves_every_declared_field_untouched() -> None:
    from eda_worker.sandbox.gateway import _without_cosmos_fields

    body = _api_record()

    assert _without_cosmos_fields(body | COSMOS_SYSTEM_FIELDS) == body
