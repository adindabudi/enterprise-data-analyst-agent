from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from eda_contracts import ArtifactKind, ArtifactRef
from eda_contracts.tasks import TaskStatus
from eda_runtime_state.messages import CanonicalMessage
from eda_runtime_state.models import QueryResultRef, TaskRecord
from eda_worker.context.repository import RuntimeTaskStateRepository
from eda_worker.context.task_state import (
    ContextSnapshot,
    QueryResultContextRef,
    TaskStateContextProvider,
)
from eda_worker.history.models import SessionPartition


class FakeContext:
    def __init__(self) -> None:
        self.options = {"task_id": "task_12345678"}
        self.instructions: list[str] = []

    def extend_instructions(self, source_id: str, instruction: str) -> None:
        assert source_id == "task-state"
        self.instructions.append(instruction)


class FakeRepository:
    async def context_snapshot(self, task_id: str) -> ContextSnapshot:
        assert task_id == "task_12345678"
        return ContextSnapshot(
            confirmed_requirements=("Analyze FY2026 revenue",),
            provenance_refs=("input-7",),
            workflow_phase="analyzing",
            pending_auth=False,
        )


@pytest.mark.asyncio
async def test_context_provider_injects_canonical_structured_state() -> None:
    provider = TaskStateContextProvider(FakeRepository())
    context = FakeContext()
    state: dict[str, object] = {}

    await provider.before_run(agent=object(), session=object(), context=context, state=state)

    [instructions] = context.instructions
    payload = json.loads(instructions.split("\n", 1)[1])
    assert payload["confirmedRequirements"] == ["Analyze FY2026 revenue"]
    assert payload["provenanceRefs"] == ["input-7"]
    assert "rows" not in instructions
    assert "dax" not in instructions.casefold()
    assert "pendingCommandText" not in payload


class HarnessContext:
    def __init__(self) -> None:
        # The harness hands over a ChatOptions object rather than a mapping.
        self.options = object()
        self.instructions: list[str] = []

    def extend_instructions(self, source_id: str, instruction: str) -> None:
        assert source_id == "task-state"
        self.instructions.append(instruction)


@pytest.mark.asyncio
async def test_context_provider_reruns_while_todos_remain() -> None:
    provider = TaskStateContextProvider(FakeRepository())
    context = HarnessContext()
    state: dict[str, object] = {"task_id": "task_12345678"}

    await provider.before_run(agent=object(), session=object(), context=context, state=state)
    await provider.before_run(agent=object(), session=object(), context=context, state=state)

    assert len(context.instructions) == 2
    assert state["task_id"] == "task_12345678"


@pytest.mark.asyncio
async def test_runtime_context_uses_owned_canonical_objective_and_task_phase() -> None:
    now = datetime.now(UTC)
    task = TaskRecord(
        id="task_12345678",
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
        session_id="ses_12345678",
        status=TaskStatus.ANALYZING,
        checkpoint_sequence=2,
        command_sequence=0,
        applied_command_sequence=0,
        sourceMessageId="msg_source_12345678",
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(days=1),
    )

    class Runtime:
        async def resolve_task(self, task_id: str) -> TaskRecord | None:
            return task if task_id == task.id else None

    class Messages:
        async def load_canonical(
            self, partition: SessionPartition, message_ids: Sequence[str]
        ) -> list[CanonicalMessage | None]:
            assert partition.values() == task.partition().values()
            assert message_ids == [task.source_message_id]
            return [
                CanonicalMessage(
                    id="msg_source_12345678",
                    tenant_id=str(task.tenant_id),
                    owner_object_id=str(task.owner_object_id),
                    session_id=task.session_id,
                    role="user",
                    text="Analyze FY2026 revenue",
                    created_at=now,
                )
            ]

    snapshot = await RuntimeTaskStateRepository(Runtime(), Messages()).context_snapshot(task.id)

    assert snapshot.confirmed_requirements == ("Analyze FY2026 revenue",)
    assert snapshot.workflow_phase == "analyzing"
    assert snapshot.pending_auth is False


class RepositoryWithRows:
    async def context_snapshot(self, task_id: str) -> ContextSnapshot:
        del task_id
        return ContextSnapshot(
            confirmed_requirements=("Export the ICU patients",),
            query_results=(
                QueryResultContextRef(
                    artifact_id="artifact-0123456789abcdef0123456789abcdef01234567",
                    version=1,
                    kind="data",
                    sha256="a" * 64,
                    display_name="query-result-1.json",
                    query="MATCH (p:patients) RETURN p.PatientId AS id",
                    row_count=44,
                    source_alias="lamna",
                ),
            ),
            workflow_phase="analyzing",
            pending_auth=False,
        )


@pytest.mark.asyncio
async def test_the_agent_is_told_the_rows_are_already_fetched_and_how_to_open_them() -> None:
    provider = TaskStateContextProvider(RepositoryWithRows())
    context = FakeContext()
    state: dict[str, object] = {}

    await provider.before_run(agent=object(), session=object(), context=context, state=state)

    instruction = context.instructions[0]
    # Without this the agent asks the user to upload data the task is already holding.
    assert "query-result-1.json" in instruction
    assert "input_artifacts" in instruction
    assert "inputs directory" in instruction
    assert "list that directory" in instruction
    assert "do not retype its rows into code" in instruction
    # The reference has to survive verbatim; the sandbox verifies the digest against it.
    assert state["queryResults"][0]["sha256"] == "a" * 64  # type: ignore[index]


@pytest.mark.asyncio
async def test_no_rows_means_no_guidance_that_would_only_confuse() -> None:
    provider = TaskStateContextProvider(FakeRepository())
    context = FakeContext()

    await provider.before_run(agent=object(), session=object(), context=context, state={})

    assert "input_artifacts" not in context.instructions[0]


@pytest.mark.asyncio
async def test_a_run_with_no_rows_and_no_source_is_told_to_say_so() -> None:
    # This run finished "successfully" having produced nothing, because nothing told
    # the agent it had neither rows nor a way to fetch them.
    provider = TaskStateContextProvider(FakeRepository(), can_read_source=False)
    context = FakeContext()

    await provider.before_run(agent=object(), session=object(), context=context, state={})

    instruction = context.instructions[0]
    assert "No source rows were handed over" in instruction
    assert "cannot read the configured source" in instruction
    assert "say what is missing" in instruction
    assert "input_artifacts" not in instruction


@pytest.mark.asyncio
async def test_a_run_that_can_still_reach_the_source_is_not_told_to_give_up() -> None:
    provider = TaskStateContextProvider(FakeRepository(), can_read_source=True)
    context = FakeContext()

    await provider.before_run(agent=object(), session=object(), context=context, state={})

    assert "No source rows were handed over" not in context.instructions[0]


@pytest.mark.asyncio
async def test_the_snapshot_carries_the_rows_the_chat_stored_on_the_task() -> None:
    now = datetime.now(UTC)
    stored = QueryResultRef(
        artifact_id="artifact-0123456789abcdef0123456789abcdef01234567",
        version=1,
        kind="data",
        sha256="b" * 64,
        display_name="query-result-1.json",
        query="MATCH (p:patients) RETURN p.PatientId AS id",
        query_sha256="c" * 64,
        row_count=44,
        source_alias="lamna",
        executed_at=now,
    )
    task = TaskRecord(
        id="task_12345678",
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
        session_id="ses_12345678",
        status=TaskStatus.ANALYZING,
        checkpoint_sequence=0,
        command_sequence=0,
        applied_command_sequence=0,
        query_results=(stored,),
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(days=1),
    )

    class Runtime:
        async def resolve_task(self, task_id: str) -> TaskRecord | None:
            return task if task_id == task.id else None

    class Messages:
        async def load_canonical(
            self, partition: SessionPartition, message_ids: Sequence[str]
        ) -> list[CanonicalMessage | None]:
            del partition, message_ids
            return []

    snapshot = await RuntimeTaskStateRepository(Runtime(), Messages()).context_snapshot(task.id)

    # The sandbox verifies the digest against the reference, so it has to survive the hop verbatim.
    assert len(snapshot.query_results) == 1
    carried = snapshot.query_results[0]
    assert carried.artifact_id == stored.artifact_id
    assert carried.sha256 == stored.sha256
    assert carried.row_count == 44


@pytest.mark.parametrize("can_read_source", [True, False])
@pytest.mark.asyncio
async def test_uploaded_inputs_reach_the_harness_without_fabricated_queries(can_read_source: bool) -> None:
    now = datetime.now(UTC)
    ref = ArtifactRef(artifact_id="artifact-uploaded12345678", version=1, kind=ArtifactKind.INPUT, sha256="d" * 64)
    task = TaskRecord(
        id="task_12345678",
        tenant_id=UUID(int=1),
        owner_object_id=UUID(int=2),
        session_id="ses_inputs12345678",
        status=TaskStatus.ANALYZING,
        checkpoint_sequence=0,
        command_sequence=0,
        applied_command_sequence=0,
        input_upload_ids=("upl_uploaded12345678",),
        input_artifacts=(ref,),
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(days=1),
    )

    class Runtime:
        async def resolve_task(self, task_id: str) -> TaskRecord | None:
            return task if task_id == task.id else None

    class Messages:
        async def load_canonical(
            self, partition: SessionPartition, message_ids: Sequence[str]
        ) -> list[CanonicalMessage | None]:
            raise AssertionError("uploads must not fabricate canonical source messages")

    repository = RuntimeTaskStateRepository(Runtime(), Messages())
    provider = TaskStateContextProvider(repository, can_read_source=can_read_source)
    context = FakeContext()
    state: dict[str, object] = {}

    await provider.before_run(agent=object(), session=object(), context=context, state=state)

    assert state.get("inputArtifacts") == [ref.model_dump(mode="json", by_alias=True)]
    assert state["queryResults"] == []
    assert state["provenanceRefs"] == []
    instruction = context.instructions[0]
    assert "execute_in_sandbox" in instruction
    assert "input_artifacts" in instruction
    assert "untrusted data" in instruction
    assert "No source rows were handed over" not in instruction
