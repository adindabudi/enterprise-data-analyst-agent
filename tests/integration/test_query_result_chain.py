"""Chat reads rows, and the hosted agent finds them. Every hop, once, in order.

Each hop has its own unit tests and each passed while the chain as a whole was
never run. The failure this guards against is silent: a task starts, the rows are
somewhere, and the agent asks the user to upload data the task is already holding.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from eda_api.chat.service import QueryRun
from eda_api.hosted_responses import HostedResponseAttempt, HostedResponseStatus
from eda_api.storage.query_results import CosmosBlobQueryResultWriter
from eda_api.task_service import TaskService
from eda_runtime_state.messages import CanonicalMessage, InMemoryMessageRepository
from eda_runtime_state.models import TaskPartition, TaskRecord
from eda_worker.context.repository import RuntimeTaskStateRepository
from eda_worker.context.task_state import TaskStateContextProvider
from eda_worker.history.models import SessionPartition
from eda_worker.sandbox.gateway import _ArtifactGatewayMetadata

PARTITION = TaskPartition(
    tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
    owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
    session_id="ses_interactive_12345678",
)
QUERY = (
    "MATCH (r:rooms)-[:rooms_has_departments]->(d:departments WHERE d.DepartmentName = 'Intensive Care Unit') "
    "OPTIONAL MATCH (p:patients)-[:patients_has_rooms]->(r) "
    "RETURN d.DepartmentId AS dept, count(*) AS total_rooms, count(p) AS occupied GROUP BY dept"
)
ROWS = json.dumps(
    [
        {"dept": 1, "total_rooms": 24, "occupied": 21},
        {"dept": 6, "total_rooms": 12, "occupied": 10},
    ],
    separators=(",", ":"),
)


class Blob:
    def __init__(self, store: dict[str, bytes], name: str) -> None:
        self._store = store
        self._name = name

    async def upload_blob(self, content: bytes, *, overwrite: bool, metadata: dict[str, str]) -> None:
        del overwrite, metadata
        self._store[self._name] = bytes(content)

    async def download_blob(self, **kwargs: Any) -> Any:
        del kwargs
        payload = self._store[self._name]

        class Stream:
            async def readall(self) -> bytes:
                return payload

        return Stream()


class Blobs:
    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}

    def get_blob_client(self, name: str) -> Blob:
        return Blob(self.store, name)


class Workspace:
    def __init__(self) -> None:
        self.items: dict[str, dict[str, Any]] = {}

    async def create_item(self, body: dict[str, Any]) -> None:
        self.items[str(body["id"])] = body


class Runtime:
    def __init__(self) -> None:
        self.tasks: dict[str, TaskRecord] = {}

    async def create_task(self, task: TaskRecord, idempotency_key: str) -> TaskRecord:
        del idempotency_key
        self.tasks[task.id] = task
        return task

    async def resolve_task(self, task_id: str) -> TaskRecord | None:
        return self.tasks.get(task_id)

    async def replace_active_attempt(
        self,
        task_id: str,
        attempt_id: str,
        *,
        expected_attempt_id: str | None,
    ) -> TaskRecord:
        task = self.tasks[task_id]
        if task.active_attempt_id != expected_attempt_id:
            raise ValueError("active attempt changed")
        updated = task.model_copy(update={"active_attempt_id": attempt_id})
        self.tasks[task_id] = updated
        return updated

    async def get_owned_task(self, partition: TaskPartition, task_id: str) -> TaskRecord | None:
        task = self.tasks.get(task_id)
        return task if task is not None and task.partition() == partition else None


class Hosted:
    def __init__(self) -> None:
        self.started: list[str] = []

    async def start(
        self,
        task_id: str,
        *,
        user_identity: str,
        previous_response_id: str | None = None,
    ) -> HostedResponseAttempt:
        del user_identity, previous_response_id
        self.started.append(task_id)
        return HostedResponseAttempt(id="resp_12345678", status=HostedResponseStatus.IN_PROGRESS)

    async def cancel(self, response_id: str, *, user_identity: str) -> HostedResponseAttempt:
        del user_identity
        return HostedResponseAttempt(id=response_id, status=HostedResponseStatus.CANCELLED)


class Context:
    def __init__(self) -> None:
        self.options = {"task_id": ""}
        self.instructions: list[str] = []

    def extend_instructions(self, source_id: str, instruction: str) -> None:
        del source_id
        self.instructions.append(instruction)


@pytest.mark.asyncio
async def test_rows_read_in_chat_reach_the_hosted_agent_as_a_file_it_can_open() -> None:
    workspace, blobs, runtime, hosted = Workspace(), Blobs(), Runtime(), Hosted()
    messages = InMemoryMessageRepository()
    source = await messages.append_user(PARTITION, "export the ICU occupancy to excel", "source-message-0021")
    service = TaskService(
        runtime,  # type: ignore[arg-type]
        messages,
        hosted,  # type: ignore[arg-type]
        query_results=CosmosBlobQueryResultWriter(workspace, blobs),  # type: ignore[arg-type]
    )

    task = await service.start_task(
        PARTITION,
        "auto-analysis:key",
        source.id,
        None,
        (QueryRun(query=QUERY, rows=ROWS, source_alias="lamna"),),
    )

    # 1. The rows are in the blob the record points at, byte for byte.
    stored = task.query_results[0]
    record = _ArtifactGatewayMetadata.model_validate(workspace.items[f"artifact::{stored.artifact_id}::v1"])
    assert blobs.store[record.blob_name] == ROWS.encode()

    # 2. The worker's own reader would accept it: the digest agrees with the record and the reference.
    assert hashlib.sha256(blobs.store[record.blob_name]).hexdigest() == record.sha256 == stored.sha256
    assert record.source_version is None

    # 3. The background response was started only after the inputs existed.
    assert hosted.started == [task.id]
    assert task.active_attempt_id == "resp_12345678"

    # 4. The agent is told the rows exist, and can address them exactly as the sandbox expects.
    class Messages:
        async def load_canonical(
            self, partition: SessionPartition, message_ids: Sequence[str]
        ) -> list[CanonicalMessage | None]:
            del partition
            return [
                CanonicalMessage(
                    id=str(message_ids[0]),
                    tenant_id=str(PARTITION.tenant_id),
                    owner_object_id=str(PARTITION.owner_object_id),
                    session_id=PARTITION.session_id,
                    role="user",
                    text="export the ICU occupancy to excel",
                    created_at=datetime.now(UTC),
                )
            ]

    provider = TaskStateContextProvider(RuntimeTaskStateRepository(runtime, Messages()))
    context = Context()
    context.options["task_id"] = task.id
    state: dict[str, object] = {}
    await provider.before_run(agent=object(), session=object(), context=context, state=state)

    carried = state["queryResults"]
    assert isinstance(carried, list) and len(carried) == 1
    entry = carried[0]
    assert isinstance(entry, dict)
    assert entry["artifactId"] == stored.artifact_id
    assert entry["sha256"] == record.sha256
    assert entry["rowCount"] == 2
    instruction = context.instructions[0]
    assert "inputs directory" in instruction
    assert "do not retype its rows into code" in instruction


@pytest.mark.asyncio
async def test_a_task_with_no_query_reaches_the_agent_exactly_as_it_did_before() -> None:
    runtime, hosted = Runtime(), Hosted()
    messages = InMemoryMessageRepository()
    source = await messages.append_user(PARTITION, "explain the schema", "source-message-0022")
    service = TaskService(
        runtime,  # type: ignore[arg-type]
        messages,
        hosted,  # type: ignore[arg-type]
        query_results=CosmosBlobQueryResultWriter(Workspace(), Blobs()),  # type: ignore[arg-type]
    )

    task = await service.start_task(PARTITION, "auto-analysis:key", source.id, None, ())

    assert task.query_results == ()
