from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest
from agent_framework import AgentSession
from eda_api.chat.service import MafInteractiveChatService
from eda_runtime_state.messages import InMemoryMessageRepository
from eda_runtime_state.models import TaskPartition

PARTITION = TaskPartition(
    tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
    owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
    session_id="ses_interactive_12345678",
)
QUERY = "MATCH (b:beds) WHERE b.unit = 'ICU' RETURN count(*) AS total"
ROWS = '{"total":24}'


class Fabric:
    alias = "lamna-healthcare"
    description = "Lamna healthcare operations ontology"

    async def schema(self, partition: TaskPartition) -> str:
        del partition
        return "beds(unit, total)"


class Graph:
    relationship_summary = "rooms_has_departments (rooms -> departments)"

    def __init__(self) -> None:
        self.executed: list[str] = []

    async def relationships(self, partition: TaskPartition) -> str:
        del partition
        return self.relationship_summary

    async def schema(self, partition: TaskPartition) -> str:
        del partition
        return "beds(unit, total)"

    async def execute(self, partition: TaskPartition, query: str) -> str:
        del partition
        self.executed.append(query)
        return ROWS


class Starter:
    def __init__(self) -> None:
        self.query_runs: list[tuple[Any, ...]] = []

    async def start_task(
        self,
        partition: TaskPartition,
        idempotency_key: str,
        message_id: str,
        handoff_context: str | None = None,
        query_runs: tuple[Any, ...] = (),
    ) -> SimpleNamespace:
        del partition, idempotency_key, message_id, handoff_context
        self.query_runs.append(query_runs)
        return SimpleNamespace(id="task_export_12345678")


class SharedStore:
    """One Redis value for the partition, so a later turn sees what an earlier one wrote."""

    def __init__(self) -> None:
        self.state: dict[str, Any] | None = None

    async def load(self, partition: TaskPartition) -> dict[str, Any] | None:
        del partition
        return self.state

    async def save(self, partition: TaskPartition, state: dict[str, Any]) -> None:
        del partition
        self.state = state


class AsksOnly:
    """Turn one: the user asks a data question and gets an answer. No handoff."""

    def create_session(self) -> AgentSession:
        return AgentSession(session_id=PARTITION.session_id)

    def run(self, messages: Any, **kwargs: Any):
        del messages
        tools = {getattr(item, "name", None): item for item in kwargs["tools"]}

        async def updates():
            await tools["query_graph"].func(query=QUERY)
            yield SimpleNamespace(text="ICU punya 24 tempat tidur.")

        return updates()


class ExportsOnly:
    """Turn two: the user says export that. The model hands off without querying again."""

    def create_session(self) -> AgentSession:
        return AgentSession(session_id=PARTITION.session_id)

    def run(self, messages: Any, **kwargs: Any):
        del messages
        tools = {getattr(item, "name", None): item for item in kwargs["tools"]}

        async def updates():
            await tools["run_deep_analysis"].func(reason="build the workbook")
            yield SimpleNamespace(text="")

        return updates()


async def turn(
    agent: Any, graph: Graph, store: SharedStore, starter: Starter, text: str, key: str
) -> tuple[str, list[Any]]:
    messages = InMemoryMessageRepository()
    source = await messages.append_user(PARTITION, text, f"source-{key}")
    service = MafInteractiveChatService(
        agent=agent,
        messages=messages,
        options={},
        fabric_query=Fabric(),
        graph_query=graph,
        analysis_starter=starter,
        session_store=store,
    )
    updates = [
        update
        async for update in service.stream(
            partition=PARTITION,
            message_id=source.id,
            history=(),
            idempotency_key=key,
        )
    ]
    return source.id, updates


@pytest.mark.asyncio
async def test_an_export_asked_for_after_the_question_still_carries_the_rows() -> None:
    graph, store, starter = Graph(), SharedStore(), Starter()

    asked, _ = await turn(AsksOnly(), graph, store, starter, "berapa tempat tidur ICU?", "interactive-turn-0101")
    exported, updates = await turn(ExportsOnly(), graph, store, starter, "export ke excel", "interactive-turn-0102")

    assert [u.event for u in updates][-1] == "analysis_started"
    assert len(starter.query_runs) == 1
    handed = starter.query_runs[0]
    assert len(handed) == 1, "the export turn handed the worker no rows to build from"
    assert handed[0].rows == ROWS
    assert handed[0].query == QUERY
    # Provenance has to name the turn that read the rows, not the one that exported them.
    assert handed[0].message_id == asked
    assert handed[0].message_id != exported
    # The second turn must not re-read the source to get them.
    assert graph.executed == [QUERY]


@pytest.mark.asyncio
async def test_a_partition_with_no_earlier_question_hands_over_nothing() -> None:
    graph, store, starter = Graph(), SharedStore(), Starter()

    await turn(ExportsOnly(), graph, store, starter, "export ke excel", "interactive-turn-0103")

    assert starter.query_runs == [()]
