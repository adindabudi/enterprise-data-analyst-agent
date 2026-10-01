from __future__ import annotations

import hashlib
from typing import Any
from uuid import UUID

import pytest
from eda_api.analysis.attempts import AttemptStatus, TaskAttempt
from eda_api.task_service import QueryRun, TaskService
from eda_contracts import ArtifactKind, ArtifactRef
from eda_runtime_state.messages import InMemoryMessageRepository
from eda_runtime_state.models import TaskPartition
from fastapi.testclient import TestClient

PARTITION = TaskPartition(
    tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
    owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
    session_id="ses_interactive_12345678",
)
ROWS = '[{"dept":1,"occupied":21},{"dept":6,"occupied":10}]'


class Writer:
    def __init__(self, failures: int = 0) -> None:
        self.written: list[tuple[str, bytes]] = []
        self._failures = failures

    async def write(self, task: Any, display_name: str, content: bytes) -> ArtifactRef:
        del task
        if self._failures > 0:
            self._failures -= 1
            raise RuntimeError("blob upload refused")
        self.written.append((display_name, content))
        return ArtifactRef(
            artifact_id=f"artifact-{len(self.written):040d}",
            version=1,
            kind=ArtifactKind.DATA,
            sha256=hashlib.sha256(content).hexdigest(),
        )


class Repository:
    def __init__(self) -> None:
        self.created: list[Any] = []

    async def get_owned_task(self, partition: TaskPartition, task_id: str) -> Any:
        return next((task for task in self.created if task.id == task_id and task.partition() == partition), None)

    async def claim_initial_dispatch(self, task_id: str) -> bool:
        task = self.created[-1]
        assert task.id == task_id
        if task.initial_dispatch_claimed:
            return False
        self.created[-1] = task.model_copy(update={"initial_dispatch_claimed": True})
        return True

    async def create_task(self, task: Any, idempotency_key: str) -> Any:
        del idempotency_key
        self.created.append(task)
        return task

    async def replace_active_attempt(
        self,
        task_id: str,
        attempt_id: str,
        *,
        expected_attempt_id: str | None,
    ) -> Any:
        assert expected_attempt_id is None
        task = self.created[-1]
        assert task.id == task_id
        updated = task.model_copy(update={"active_attempt_id": attempt_id})
        self.created[-1] = updated
        return updated


class DurableClient:
    def __init__(self) -> None:
        self.scheduled: list[str] = []

    async def start(self, task_id: str) -> TaskAttempt:
        self.scheduled.append(task_id)
        return TaskAttempt(id=f"resp_{task_id[5:]}", status=AttemptStatus.QUEUED)


async def _start(writer: Writer | None, runs: tuple[QueryRun, ...]) -> tuple[Any, Repository, DurableClient]:
    messages = InMemoryMessageRepository()
    source = await messages.append_user(PARTITION, "export ICU ke excel", "source-message-0020")
    repository, durable = Repository(), DurableClient()
    service = TaskService(
        repository,  # type: ignore[arg-type]
        messages,
        durable,  # type: ignore[arg-type]
        query_results=writer,  # type: ignore[arg-type]
    )
    task = await service.start_task(PARTITION, "auto-analysis:key", source.id, None, runs)
    return task, repository, durable


@pytest.mark.asyncio
async def test_the_stored_rows_are_attached_to_the_task_before_it_is_created() -> None:
    writer = Writer()
    query = "MATCH (r:rooms) RETURN count(*) AS c"

    task, repository, durable = await _start(writer, (QueryRun(query=query, rows=ROWS, source_alias="lamna"),))

    assert [name for name, _ in writer.written] == ["query-result-1.json"]
    stored = task.query_results[0]
    assert stored.row_count == 2
    assert stored.query == query
    assert stored.query_sha256 == hashlib.sha256(query.encode()).hexdigest()
    assert stored.source_alias == "lamna"
    # The orchestration must never start against a task whose inputs are still being written.
    assert repository.created[0].query_results == task.query_results
    assert durable.scheduled == [task.id]


@pytest.mark.asyncio
async def test_a_task_still_starts_when_the_rows_could_not_be_stored() -> None:
    writer = Writer(failures=1)

    task, _, durable = await _start(writer, (QueryRun(query="MATCH (n) RETURN 1", rows=ROWS),))

    # A durable run without its rows is worth more than no durable run at all.
    assert task.query_results == ()
    assert durable.scheduled == [task.id]


@pytest.mark.asyncio
async def test_nothing_is_stored_when_the_deployment_has_no_writer() -> None:
    task, _, durable = await _start(None, (QueryRun(query="MATCH (n) RETURN 1", rows=ROWS),))

    assert task.query_results == ()
    assert durable.scheduled == [task.id]


def test_the_application_hands_the_writer_to_the_task_service(settings: Any) -> None:
    from eda_api.main import create_app
    from eda_api.storage.uploads import InMemoryBlobStore, InMemoryUploadRepository, UploadService
    from eda_runtime_state.tasks import InMemoryRuntimeStateRepository

    writer = Writer()
    application = create_app(
        settings_override=settings,
        upload_service_override=UploadService(
            blob_store=InMemoryBlobStore(),
            upload_repository=InMemoryUploadRepository(),
            upload_limit_bytes=settings.upload_limit_bytes,
        ),
        runtime_repository_override=InMemoryRuntimeStateRepository(),
        message_repository_override=InMemoryMessageRepository(),
        query_result_writer_override=writer,  # type: ignore[arg-type]
    )

    with TestClient(application, base_url="https://analyst.example.test"):
        # Without this seam the rows are written nowhere and the handoff silently loses them.
        assert application.state.task_service.query_results is writer
