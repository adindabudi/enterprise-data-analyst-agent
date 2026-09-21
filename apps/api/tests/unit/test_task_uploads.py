from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import pytest
from azure.core.exceptions import ServiceRequestError, ServiceResponseError
from azure.cosmos.exceptions import CosmosHttpResponseError
from eda_api.auth.models import Principal
from eda_api.storage.inputs import CosmosBlobInputArtifactWriter
from eda_api.storage.uploads import InMemoryUploadRepository, UploadNotReady, UploadService
from eda_api.task_service import TaskService
from eda_contracts import ArtifactKind
from eda_contracts.tasks import TaskStatus
from eda_runtime_state.messages import InMemoryMessageRepository
from eda_runtime_state.models import TaskPartition, TaskRecord, deterministic_task_id
from eda_runtime_state.tasks import InMemoryRuntimeStateRepository, RuntimeStateConflict

from apps.api.tests.unit.storage.test_input_artifacts import Blobs, Workspace
from apps.api.tests.unit.storage.test_uploads import OWNER, SESSION_ID, ScannedBlobStore, chunks
from apps.api.tests.unit.test_task_service import FakeHostedClient


class ObservingHostedClient(FakeHostedClient):
    def __init__(self, runtime: InMemoryRuntimeStateRepository) -> None:
        super().__init__(runtime)
        self.runtime = runtime
        self.submitted_tasks: list[TaskRecord] = []
        self.ambiguous_failure = False
        self.fail_before_submission: Exception | None = None
        self.ambiguous_error: Exception = TimeoutError("response acknowledgement was lost")

    async def start(self, task_id: str, *, user_identity: str, previous_response_id: str | None = None):
        if self.fail_before_submission is not None:
            error, self.fail_before_submission = self.fail_before_submission, None
            raise error
        task = await self.runtime.resolve_task(task_id)
        assert task is not None
        self.submitted_tasks.append(task)
        attempt = await super().start(task_id, user_identity=user_identity, previous_response_id=previous_response_id)
        await asyncio.sleep(0)
        if self.ambiguous_failure:
            raise self.ambiguous_error
        return attempt


class Pipeline:
    def __init__(self) -> None:
        self.runtime = InMemoryRuntimeStateRepository()
        self.messages = InMemoryMessageRepository()
        self.quarantine = ScannedBlobStore()
        self.quarantine.scan_result = "No threats found"
        self.uploads = UploadService(
            blob_store=self.quarantine,
            upload_repository=InMemoryUploadRepository(),
            upload_limit_bytes=50 * 1024 * 1024,
        )
        self.workspace, self.blobs = Workspace(), Blobs()
        self.writer = CosmosBlobInputArtifactWriter(self.workspace, self.blobs)
        self.hosted = ObservingHostedClient(self.runtime)
        self.service = self.new_service()

    def new_service(self) -> TaskService:
        return TaskService(
            self.runtime,
            self.messages,
            self.hosted,
            uploads=self.uploads,
            input_artifact_writer=self.writer,
        )

    async def source(self, owner: Principal = OWNER, session_id: str = SESSION_ID):
        partition = TaskPartition(
            tenant_id=owner.tenant_id, owner_object_id=owner.owner_object_id, session_id=session_id
        )
        upload = await self.uploads.create_quarantine_upload(owner, session_id, "data.csv", chunks([b"value\n42\n"]))
        message = await self.messages.append_user(partition, "Analyze the attached data", "upload-source-message")
        return partition, message.id, upload.id


@pytest.mark.asyncio
async def test_verified_inputs_are_persisted_on_the_canonical_task_before_dispatch() -> None:
    pipeline = Pipeline()
    partition, message_id, upload_id = await pipeline.source()

    task = await pipeline.service.start_task(partition, "upload-task-key", message_id, input_upload_ids=(upload_id,))

    assert task.input_upload_ids == (upload_id,)
    assert len(task.input_artifacts) == 1
    assert task.input_artifacts[0].kind is ArtifactKind.INPUT
    assert task.query_results == ()
    assert pipeline.hosted.submitted_tasks[0].input_artifacts == task.input_artifacts
    persisted = await pipeline.runtime.resolve_task(task.id)
    assert persisted is not None and persisted.input_artifacts == task.input_artifacts


@pytest.mark.parametrize("scan_result", [None, "Malicious", "Error", "Not scanned", "unknown"])
@pytest.mark.asyncio
async def test_unclean_inputs_never_reach_task_submission(scan_result: str | None) -> None:
    pipeline = Pipeline()
    partition, message_id, upload_id = await pipeline.source()
    pipeline.quarantine.scan_result = scan_result

    with pytest.raises(UploadNotReady):
        await pipeline.service.start_task(partition, "blocked-upload-key", message_id, input_upload_ids=(upload_id,))

    assert pipeline.quarantine.downloads == 0
    assert pipeline.hosted.started == []
    assert pipeline.blobs.items == {}
    assert (
        await pipeline.runtime.get_owned_task(partition, deterministic_task_id(partition, "blocked-upload-key")) is None
    )


@pytest.mark.parametrize("scope", ["owner", "tenant", "session"])
@pytest.mark.asyncio
async def test_foreign_upload_cannot_be_bound_to_a_task(scope: str) -> None:
    pipeline = Pipeline()
    partition, message_id, _ = await pipeline.source()
    foreign = OWNER.model_copy(update={"owner_object_id": UUID(int=5)}) if scope == "owner" else OWNER
    if scope == "tenant":
        foreign = OWNER.model_copy(update={"tenant_id": UUID(int=6)})
    session_id = "ses_another_session01" if scope == "session" else SESSION_ID
    _, _, upload_id = await pipeline.source(foreign, session_id)

    with pytest.raises(LookupError):
        await pipeline.service.start_task(partition, "foreign-upload-key", message_id, input_upload_ids=(upload_id,))

    assert pipeline.hosted.started == []
    assert pipeline.quarantine.downloads == 0


@pytest.mark.asyncio
async def test_task_retry_reuses_original_inputs_and_does_not_resubmit() -> None:
    pipeline = Pipeline()
    partition, message_id, upload_id = await pipeline.source()
    first = await pipeline.service.start_task(partition, "retry-upload-key", message_id, input_upload_ids=(upload_id,))
    pipeline.quarantine.scan_result = None

    retried = await pipeline.new_service().start_task(
        partition, "retry-upload-key", message_id, input_upload_ids=(upload_id,)
    )

    assert retried == first
    assert len(pipeline.hosted.started) == 1


@pytest.mark.parametrize(
    "error_type", [ServiceRequestError, httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout]
)
@pytest.mark.asyncio
async def test_known_non_submission_can_retry_without_promoting_inputs_again(error_type: type[Exception]) -> None:
    pipeline = Pipeline()
    partition, message_id, upload_id = await pipeline.source()
    pipeline.hosted.fail_before_submission = error_type("connection could not be established")

    with pytest.raises(error_type):
        await pipeline.service.start_task(partition, "not-submitted-key", message_id, input_upload_ids=(upload_id,))
    assert pipeline.hosted.started == []
    pipeline.quarantine.scan_result = None

    retried = await pipeline.new_service().start_task(
        partition, "not-submitted-key", message_id, input_upload_ids=(upload_id,)
    )

    assert retried.active_attempt_id is not None
    assert len(pipeline.hosted.started) == 1
    assert pipeline.quarantine.downloads == 1
    assert pipeline.quarantine.downloads == 1


@pytest.mark.asyncio
async def test_idempotency_key_cannot_change_the_selected_uploads() -> None:
    pipeline = Pipeline()
    partition, message_id, upload_id = await pipeline.source()
    await pipeline.service.start_task(partition, "bound-upload-key", message_id, input_upload_ids=(upload_id,))
    _, _, another_upload_id = await pipeline.source()

    for upload_ids in [(), (another_upload_id,)]:
        with pytest.raises(RuntimeStateConflict):
            await pipeline.service.start_task(partition, "bound-upload-key", message_id, input_upload_ids=upload_ids)

    assert len(pipeline.hosted.started) == 1


@pytest.mark.asyncio
async def test_partial_input_promotion_is_retriable_without_duplicate_submission() -> None:
    pipeline = Pipeline()
    partition, message_id, upload_id = await pipeline.source()
    pipeline.workspace.fail_next_write = True

    with pytest.raises(CosmosHttpResponseError):
        await pipeline.service.start_task(partition, "partial-upload-key", message_id, input_upload_ids=(upload_id,))
    assert pipeline.hosted.started == []
    task = await pipeline.new_service().start_task(
        partition, "partial-upload-key", message_id, input_upload_ids=(upload_id,)
    )

    assert len(task.input_artifacts) == 1
    assert len(pipeline.hosted.started) == 1
    assert len(pipeline.blobs.items) == len(pipeline.workspace.items) == 1


@pytest.mark.asyncio
async def test_concurrent_upload_task_submissions_dispatch_at_most_once() -> None:
    pipeline = Pipeline()
    partition, message_id, upload_id = await pipeline.source()

    results = await asyncio.gather(
        pipeline.service.start_task(partition, "concurrent-upload-key", message_id, input_upload_ids=(upload_id,)),
        pipeline.new_service().start_task(
            partition, "concurrent-upload-key", message_id, input_upload_ids=(upload_id,)
        ),
        return_exceptions=True,
    )

    assert len(pipeline.hosted.started) == 1
    assert all(isinstance(result, (TaskRecord, RuntimeStateConflict)) for result in results)
    retried = await pipeline.service.start_task(
        partition, "concurrent-upload-key", message_id, input_upload_ids=(upload_id,)
    )
    assert retried.active_attempt_id is not None
    assert len(pipeline.hosted.started) == 1


@pytest.mark.asyncio
async def test_upload_task_idempotency_is_partition_scoped() -> None:
    pipeline = Pipeline()
    partition, message_id, upload_id = await pipeline.source()
    first = await pipeline.service.start_task(partition, "shared-upload-key", message_id, input_upload_ids=(upload_id,))
    other = OWNER.model_copy(update={"owner_object_id": UUID(int=9)})
    other_partition, other_message_id, other_upload_id = await pipeline.source(other)

    second = await pipeline.service.start_task(
        other_partition, "shared-upload-key", other_message_id, input_upload_ids=(other_upload_id,)
    )

    assert second.id != first.id
    assert second.owner_object_id == other.owner_object_id
    assert second.input_artifacts != first.input_artifacts
    assert len(pipeline.hosted.started) == 2


@pytest.mark.parametrize("error_type", [TimeoutError, ServiceResponseError, httpx.ReadTimeout, httpx.WriteError])
@pytest.mark.asyncio
async def test_ambiguous_dispatch_failure_is_not_blindly_resubmitted(error_type: type[Exception]) -> None:
    pipeline = Pipeline()
    partition, message_id, upload_id = await pipeline.source()
    pipeline.hosted.ambiguous_failure = True
    pipeline.hosted.ambiguous_error = error_type("response acknowledgement was lost")

    with pytest.raises(error_type):
        await pipeline.service.start_task(partition, "ambiguous-upload-key", message_id, input_upload_ids=(upload_id,))
    with pytest.raises(RuntimeStateConflict):
        await pipeline.new_service().start_task(
            partition, "ambiguous-upload-key", message_id, input_upload_ids=(upload_id,)
        )

    assert len(pipeline.hosted.started) == 1


@pytest.mark.parametrize("trigger", ["retry", "reconcile"])
@pytest.mark.asyncio
async def test_abandoned_ambiguous_dispatch_reaches_canonical_failure_without_resubmitting(trigger: str) -> None:
    pipeline = Pipeline()
    partition, message_id, upload_id = await pipeline.source()
    pipeline.hosted.ambiguous_failure = True
    key = "expired-dispatch-key"
    with pytest.raises(TimeoutError):
        await pipeline.service.start_task(partition, key, message_id, input_upload_ids=(upload_id,))
    pending = await pipeline.runtime.resolve_task(deterministic_task_id(partition, key))
    assert pending is not None and pending.initial_dispatch_claimed
    assert await pipeline.service.reconcile_abandoned_task(pending) == pending

    stale = pending.model_copy(update={"updated_at": datetime.now(UTC) - timedelta(minutes=11)})
    pipeline.runtime = InMemoryRuntimeStateRepository()
    await pipeline.runtime.create_task(stale, key)
    service = pipeline.new_service()
    if trigger == "retry":
        result = await service.start_task(partition, key, message_id, input_upload_ids=(upload_id,))
    else:
        result = await service.reconcile_abandoned_task(stale)

    assert result.status is TaskStatus.FAILED
    assert result.initial_dispatch_claimed is True
    assert result.active_attempt_id is None
    assert result.input_artifacts == pending.input_artifacts
    assert await pipeline.runtime.resolve_task(result.id) == result
    assert await service.start_task(partition, key, message_id, input_upload_ids=(upload_id,)) == result
    assert len(pipeline.hosted.started) == 1
    assert pipeline.quarantine.downloads == 1


@pytest.mark.asyncio
async def test_stale_dispatch_snapshot_cannot_fail_a_newly_attached_attempt() -> None:
    pipeline = Pipeline()
    partition, message_id, upload_id = await pipeline.source()
    task = await pipeline.service.start_task(partition, "dispatch-race-key", message_id, input_upload_ids=(upload_id,))
    stale = task.model_copy(update={"active_attempt_id": None, "updated_at": datetime.now(UTC) - timedelta(minutes=11)})

    result = await pipeline.service.reconcile_abandoned_task(stale)

    assert result.active_attempt_id == task.active_attempt_id
    assert result.status is TaskStatus.PLANNING
    assert len(pipeline.hosted.started) == 1


@pytest.mark.asyncio
async def test_late_dispatch_acknowledgement_is_cancelled_after_canonical_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline = Pipeline()
    partition, message_id, upload_id = await pipeline.source()
    original_start = pipeline.hosted.start

    async def late_start(task_id: str, *, user_identity: str, previous_response_id: str | None = None):
        attempt = await original_start(task_id, user_identity=user_identity, previous_response_id=previous_response_id)
        await pipeline.runtime.transition_task(task_id, TaskStatus.FAILED, 0)
        return attempt

    monkeypatch.setattr(pipeline.hosted, "start", late_start)
    result = await pipeline.service.start_task(
        partition, "late-dispatch-key", message_id, input_upload_ids=(upload_id,)
    )

    assert result.status is TaskStatus.FAILED
    assert result.active_attempt_id is None
    assert [attempt_id for attempt_id, _ in pipeline.hosted.cancelled] == ["resp_attempt00000001"]
    assert len(pipeline.hosted.started) == 1
