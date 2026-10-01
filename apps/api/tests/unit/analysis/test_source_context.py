"""The pinned schema snapshot: in the agent's instructions for a linked user, withheld from everyone else."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from eda_api.analysis.source_context import SOURCE_TEXT_NOTICE, SourceSnapshotContextProvider
from eda_api.fabric_auth.snapshot import (
    SchemaSnapshot,
    SnapshotEntity,
    SnapshotProperty,
    SnapshotRelationship,
    snapshot_id,
)
from eda_contracts.tasks import TaskStatus
from eda_fabric_auth.msal_cache import FabricAuthorizationRequired
from eda_runtime_state.models import TaskPartition, TaskRecord
from eda_runtime_state.tasks import InMemoryRuntimeStateRepository

IDENTIFIER = "0f8fad5b-d9cb-469f-a165-70867728950e"
SNAPSHOT = SchemaSnapshot(
    id=snapshot_id("lamna"),
    alias="lamna",
    workspace_id=UUID(int=5),
    ontology_id=UUID(int=6),
    graph_model_id=UUID(int=7),
    generated_at=datetime(2026, 10, 1, tzinfo=UTC),
    entities=(
        SnapshotEntity(
            name="hospitals",
            description=f"Hospitals, documented at https://wiki.example/{IDENTIFIER}",
            properties=(
                SnapshotProperty(name="HospitalId", value_type="BigInt", key=True),
                SnapshotProperty(name="HospitalName", value_type="String", stored_values=("Cascade General",)),
            ),
        ),
    ),
    relationships=(SnapshotRelationship(name="departments_has_hospitals", source="departments", target="hospitals"),),
)


class Tokens:
    def __init__(self) -> None:
        self.linked: set[UUID] = set()
        self.calls: list[TaskPartition] = []
        self.error: BaseException | None = None

    async def acquire(self, partition: TaskPartition) -> str:
        self.calls.append(partition)
        if self.error is not None:
            raise self.error
        if partition.owner_object_id not in self.linked:
            raise FabricAuthorizationRequired("link Fabric")
        return "owner-token"


@dataclass
class Context:
    options: dict[str, object]
    added: list[tuple[str, str]] = field(default_factory=list)

    def extend_instructions(self, source_id: str, instructions: str) -> None:
        self.added.append((source_id, instructions))


async def _task(repository: InMemoryRuntimeStateRepository, task_id: str, owner: int) -> TaskRecord:
    now = datetime.now(UTC)
    return await repository.create_task(
        TaskRecord(
            id=task_id,
            tenant_id=UUID(int=1),
            owner_object_id=UUID(int=owner),
            session_id=f"ses_{task_id[5:]}000000",
            status=TaskStatus.ANALYZING,
            checkpoint_sequence=1,
            command_sequence=0,
            applied_command_sequence=0,
            created_at=now,
            updated_at=now,
            expires_at=now + timedelta(days=1),
        ),
        f"request-{task_id}",
    )


async def _provider(tokens: Tokens) -> tuple[SourceSnapshotContextProvider, InMemoryRuntimeStateRepository]:
    repository = InMemoryRuntimeStateRepository()
    await _task(repository, "task_linkedowner01", owner=2)
    await _task(repository, "task_unlinkedown01", owner=3)
    provider = SourceSnapshotContextProvider(
        snapshot=SNAPSHOT,
        description="Synthetic hospital operations",
        timeseries=False,
        tasks=repository,
        tokens=tokens,
    )
    return provider, repository


async def _run(provider: SourceSnapshotContextProvider, task_id: str, state: dict[str, Any] | None = None) -> str:
    context = Context(options={"task_id": task_id})
    await provider.before_run(
        agent=object(), session=object(), context=context, state=state if state is not None else {}
    )
    [(source_id, instructions)] = context.added
    assert source_id == "fabric-source"
    return instructions


@pytest.mark.asyncio
async def test_a_linked_user_gets_the_whole_schema_as_reference_data() -> None:
    tokens = Tokens()
    tokens.linked.add(UUID(int=2))
    provider, _ = await _provider(tokens)

    instructions = await _run(provider, "task_linkedowner01")

    assert instructions.startswith(SOURCE_TEXT_NOTICE)
    assert "HospitalName String = 'Cascade General'" in instructions
    assert "(:departments)-[:departments_has_hospitals]->(:hospitals)" in instructions
    assert "https://" not in instructions and IDENTIFIER not in instructions


@pytest.mark.asyncio
async def test_a_user_without_a_fabric_link_sees_no_schema() -> None:
    provider, _ = await _provider(Tokens())

    instructions = await _run(provider, "task_unlinkedown01")

    assert "HospitalName" not in instructions and "Cascade General" not in instructions
    assert "query_graph" in instructions and "'lamna'" in instructions


@pytest.mark.asyncio
async def test_a_confirmed_link_is_remembered_but_a_missing_one_is_asked_again() -> None:
    tokens = Tokens()
    provider, _ = await _provider(tokens)

    await _run(provider, "task_unlinkedown01")
    tokens.linked.add(UUID(int=3))
    # The user linked Fabric between turns; the next turn has to see the schema.
    linked = await _run(provider, "task_unlinkedown01")
    await _run(provider, "task_unlinkedown01")

    assert "HospitalName" in linked
    assert len(tokens.calls) == 2


@pytest.mark.asyncio
async def test_a_failed_link_check_withholds_the_schema_for_this_run_only() -> None:
    tokens = Tokens()
    tokens.linked.add(UUID(int=2))
    tokens.error = RuntimeError("key vault unavailable")
    provider, _ = await _provider(tokens)

    withheld = await _run(provider, "task_linkedowner01")
    tokens.error = None
    shown = await _run(provider, "task_linkedowner01")

    assert "HospitalName" not in withheld and "HospitalName" in shown


@pytest.mark.asyncio
async def test_the_task_id_outlives_the_first_run_of_the_harness_loop() -> None:
    tokens = Tokens()
    tokens.linked.add(UUID(int=2))
    provider, _ = await _provider(tokens)
    state: dict[str, Any] = {}

    await _run(provider, "task_linkedowner01", state)
    context = Context(options={})
    await provider.before_run(agent=object(), session=object(), context=context, state=state)

    assert state["task_id"] == "task_linkedowner01"
    assert "HospitalName" in context.added[0][1]


@pytest.mark.asyncio
async def test_a_run_without_a_trusted_task_is_refused() -> None:
    provider, _ = await _provider(Tokens())

    with pytest.raises(ValueError, match="trusted task_id"):
        await provider.before_run(agent=object(), session=object(), context=Context(options={}), state={})
