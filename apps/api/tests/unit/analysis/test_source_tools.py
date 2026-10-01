"""The harness's source tools: the query the model wrote, evidence kept whole, identical reads reused.

The tools carry over what the retired interactive chat guaranteed (sanitized source
text, honest failure reasons, one token acquisition per read) and add what one
runtime needs: task-scoped reuse, stored evidence and bounded previews. There is
no discovery tool: the schema is pinned in the instructions (see test_source_context).
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from eda_api.analysis.source_tools import (
    AUTHORIZATION_REQUIRED_DETAIL,
    GRAPH_QUERY_DESCRIPTION,
    INCOMPLETE_RESULT_NOTE,
    REPAIRED_QUERY_NOTE,
    TIMESERIES_QUERY_DESCRIPTION,
    GraphQueryArguments,
    SourceTools,
    TaskCoalescers,
    TimeSeriesQueryArguments,
    _failure_reason,
    _status_detail,
    source_reference_text,
)
from eda_api.fabric_auth.graph import QueryEvidence
from eda_contracts import ArtifactKind, ArtifactRef
from eda_contracts.controls import CommandKind
from eda_contracts.tasks import TaskStatus
from eda_fabric_auth.msal_cache import FabricAuthorizationRequired
from eda_runtime_state.events import EventDraft
from eda_runtime_state.models import TaskPartition, TaskRecord
from eda_runtime_state.tasks import InMemoryRuntimeStateRepository

TASK_ID = "task_sourcetools01"
IDENTIFIER = "0f8fad5b-d9cb-469f-a165-70867728950e"
ROWS = '[{"room":"A","occupied":21},{"room":"B","occupied":10}]'
DESCRIPTION = f"Lamna healthcare ontology https://fabric.example/{IDENTIFIER} via search_ontology"


def _evidence(
    complete: str = ROWS, *, preview: str | None = None, row_count: int | None = 2, incomplete: bool = False
) -> QueryEvidence:
    shown = complete if preview is None else preview
    return QueryEvidence(
        complete=complete,
        preview=shown,
        row_count=row_count,
        preview_truncated=shown != complete,
        source_incomplete=incomplete,
    )


class Source:
    def __init__(self) -> None:
        self.queries: list[tuple[TaskPartition, str]] = []
        self.error: BaseException | None = None
        self.result = _evidence()
        self.gate: asyncio.Event | None = None

    async def execute_evidence(self, partition: TaskPartition, query: str) -> QueryEvidence:
        self.queries.append((partition, query))
        if self.gate is not None:
            await self.gate.wait()
        if self.error is not None:
            raise self.error
        return self.result


class Writer:
    def __init__(self, *, failing: bool = False) -> None:
        self.written: list[tuple[str, bytes]] = []
        self.failing = failing

    async def write(self, task: TaskRecord, display_name: str, content: bytes) -> ArtifactRef:
        del task
        if self.failing:
            raise RuntimeError("blob upload refused")
        self.written.append((display_name, content))
        return ArtifactRef(
            artifact_id=f"artifact-{len(self.written):040d}",
            version=1,
            kind=ArtifactKind.DATA,
            sha256=hashlib.sha256(content).hexdigest(),
        )


class Events:
    def __init__(self) -> None:
        self.drafts: list[EventDraft] = []

    async def append(self, draft: EventDraft) -> object:
        self.drafts.append(draft)
        return draft


@dataclass
class Context:
    kwargs: dict[str, object]
    metadata: dict[str, object]


@dataclass
class Setup:
    tools: SourceTools
    graph: Source
    timeseries: Source
    writer: Writer
    events: Events
    repository: InMemoryRuntimeStateRepository
    task: TaskRecord


async def _setup(*, timeseries: bool = True, writer: Writer | None = None) -> Setup:
    repository = InMemoryRuntimeStateRepository()
    now = datetime.now(UTC)
    task = await repository.create_task(
        TaskRecord(
            id=TASK_ID,
            tenant_id=UUID(int=1),
            owner_object_id=UUID(int=2),
            session_id="ses_sourcetools00001",
            status=TaskStatus.ANALYZING,
            checkpoint_sequence=1,
            command_sequence=0,
            applied_command_sequence=0,
            sourceMessageId="msg_question000001",
            created_at=now,
            updated_at=now,
            expires_at=now + timedelta(days=1),
        ),
        "request-sourcetools",
    )
    graph, series, events = Source(), Source(), Events()
    store = writer or Writer()
    tools = SourceTools(
        alias="lamna",
        description=DESCRIPTION,
        graph=graph,
        timeseries=series if timeseries else None,
        tasks=repository,
        ledger=repository,
        writer=store,
        events=events,
    )
    return Setup(tools, graph, series, store, events, repository, task)


def _context(task_id: str = TASK_ID) -> Context:
    return Context(kwargs={"task_id": task_id}, metadata={"call_id": "call_12345678"})


def _tool(setup: Setup, name: str) -> Any:
    return next(tool for tool in setup.tools.tools() if tool.name == name)


@pytest.mark.asyncio
async def test_the_harness_gets_one_query_tool_per_route_and_no_discovery_tool() -> None:
    setup = await _setup()

    assert [tool.name for tool in setup.tools.tools()] == ["query_graph", "query_timeseries"]


@pytest.mark.asyncio
async def test_without_a_kql_database_the_time_series_tool_is_not_offered() -> None:
    setup = await _setup(timeseries=False)

    assert [tool.name for tool in setup.tools.tools()] == ["query_graph"]
    result = await setup.tools.timeseries_query(TASK_ID, TimeSeriesQueryArguments(query="T | count"))
    assert result["status"] == "error"


@pytest.mark.parametrize("description", [GRAPH_QUERY_DESCRIPTION, TIMESERIES_QUERY_DESCRIPTION])
def test_a_route_description_names_no_single_ontology_and_points_at_the_snapshot(description: str) -> None:
    for specific in ("lamna", "Patient", "healthcare", "rooms", "VitalSigns"):
        assert specific not in description
    assert "schema snapshot" in description


def test_the_graph_description_carries_the_gql_rules_verified_against_the_engine() -> None:
    # Checked against a Fabric graph on 2026-10-01: NEXT FILTER is how a grouped result is filtered,
    # FILTER right after GROUP BY does not parse, CASE WHEN is unsupported, and years or months are
    # refused as durations while weeks are accepted. Grouping keys are LET variables (Learn: GROUP BY <variable>).
    assert "GROUP BY k NEXT FILTER c > 10" in GRAPH_QUERY_DESCRIPTION
    assert "FILTER placed directly after GROUP BY does not parse" in GRAPH_QUERY_DESCRIPTION
    assert "CASE WHEN is not supported" in GRAPH_QUERY_DESCRIPTION
    assert "years and months are refused" in GRAPH_QUERY_DESCRIPTION
    assert "duration('P3391W')" in GRAPH_QUERY_DESCRIPTION
    assert "Bind every grouping value with LET before RETURN" in GRAPH_QUERY_DESCRIPTION
    assert "bound in RETURN or LET" not in GRAPH_QUERY_DESCRIPTION


def test_the_time_series_description_carries_the_kql_rules_verified_against_the_engine() -> None:
    # Each of these failed or silently returned nothing against an Eventhouse on 2026-10-01 when written the obvious way.
    for rule in (
        "anchor a relative window on the data",
        "Cast a dynamic value with tostring()",
        "only equality, written $left.Key == $right.Key",
        "put order by or serialize before them",
        "bracket it as ['last']",
    ):
        assert rule in TIMESERIES_QUERY_DESCRIPTION


@pytest.mark.asyncio
async def test_source_text_carries_no_link_identifier_or_provider_tool_into_a_tool_description() -> None:
    setup = await _setup()

    for tool in setup.tools.tools():
        assert "https://" not in tool.description
        assert IDENTIFIER not in tool.description
        assert "search_ontology" not in tool.description
        assert "'lamna'" in tool.description


@pytest.mark.asyncio
async def test_the_graph_route_runs_the_query_the_model_wrote_and_stores_the_complete_rows() -> None:
    setup = await _setup()
    query = "  MATCH (r:Room) RETURN r.name AS v, count(*) AS c GROUP BY v  "

    result = await _tool(setup, "query_graph").func(_context(), query=query)

    assert setup.graph.queries == [(setup.task.partition(), query.strip())]
    assert result["status"] == "ok"
    assert result["rows"] == ROWS and result["rowCount"] == 2 and result["previewTruncated"] is False
    assert "sourceResultIncomplete" not in result
    [(display_name, content)] = setup.writer.written
    assert content == ROWS.encode()
    assert display_name.startswith("gql-")
    stored = await setup.repository.resolve_task(TASK_ID)
    assert stored is not None
    [ref] = stored.query_results
    assert ref.artifact_id == result["evidence"]["artifactId"]
    assert ref.query == query.strip()
    assert ref.query_sha256 == hashlib.sha256(query.strip().encode()).hexdigest()
    assert ref.row_count == 2 and ref.source_alias == "lamna" and ref.message_id == "msg_question000001"


@pytest.mark.asyncio
async def test_the_time_series_route_runs_the_kql_the_model_wrote_and_stores_its_rows() -> None:
    setup = await _setup()
    query = "VitalSignsReadings | summarize max(HeartRate) by EquipmentId"

    result = await _tool(setup, "query_timeseries").func(_context(), query=query)

    assert setup.timeseries.queries == [(setup.task.partition(), query)]
    assert setup.graph.queries == []
    assert result["status"] == "ok"
    [(display_name, _)] = setup.writer.written
    assert display_name.startswith("kql-")


@pytest.mark.asyncio
async def test_an_incomplete_source_result_is_flagged_so_no_total_is_reported_from_it() -> None:
    setup = await _setup()
    setup.timeseries.result = _evidence(incomplete=True)

    result = await setup.tools.timeseries_query(TASK_ID, TimeSeriesQueryArguments(query="T | take 5000"))

    assert result["sourceResultIncomplete"] is True
    assert result["incompleteNote"] == INCOMPLETE_RESULT_NOTE


@pytest.mark.asyncio
async def test_a_repaired_query_is_shown_to_the_model_and_recorded_as_what_ran() -> None:
    setup = await _setup()
    written = "MATCH (h:Hospital) RETURN h.Name AS n, count(*) AS c GROUP BY n"
    executed = "MATCH (h:Hospital) LET n = h.Name RETURN n, count(*) AS c GROUP BY n"
    setup.graph.result = QueryEvidence(
        complete=ROWS, preview=ROWS, row_count=2, preview_truncated=False, executed_query=executed
    )

    result = await setup.tools.graph_query(TASK_ID, GraphQueryArguments(query=written))

    assert result["executedQuery"] == executed and result["repairNote"] == REPAIRED_QUERY_NOTE
    stored = await setup.repository.resolve_task(TASK_ID)
    assert stored is not None
    [ref] = stored.query_results
    # Provenance names the statement the rows came from, not the one the engine refused.
    assert ref.query == executed
    assert ref.query_sha256 == hashlib.sha256(executed.encode()).hexdigest()


@pytest.mark.asyncio
async def test_no_rows_is_returned_as_an_answer() -> None:
    setup = await _setup()
    setup.graph.result = _evidence("[]", row_count=0)

    result = await setup.tools.graph_query(
        TASK_ID, GraphQueryArguments(query="MATCH (n:Room WHERE n.x = 'y') RETURN n")
    )

    assert result["status"] == "ok" and result["rows"] == "[]" and result["rowCount"] == 0


@pytest.mark.asyncio
async def test_a_bounded_preview_is_marked_and_points_at_the_complete_evidence() -> None:
    setup = await _setup()
    complete = "[" + ",".join(f'{{"n":{index}}}' for index in range(500)) + "]"
    setup.graph.result = _evidence(complete, preview='[{"n":0}]', row_count=500)

    result = await setup.tools.graph_query(TASK_ID, GraphQueryArguments(query="MATCH (n:Row) RETURN n.n AS n"))

    assert result["previewTruncated"] is True and result["rowCount"] == 500
    assert setup.writer.written[0][1] == complete.encode()
    assert "evidence" in result


@pytest.mark.asyncio
async def test_an_identical_read_in_one_task_reaches_the_source_once() -> None:
    setup = await _setup()
    arguments = GraphQueryArguments(query="MATCH (p:Patient) RETURN count(*) AS c")
    setup.graph.gate = asyncio.Event()

    first = asyncio.create_task(setup.tools.graph_query(TASK_ID, arguments))
    second = asyncio.create_task(setup.tools.graph_query(TASK_ID, arguments))
    await asyncio.sleep(0)
    setup.graph.gate.set()
    results = await asyncio.gather(first, second)
    third = await setup.tools.graph_query(TASK_ID, arguments)

    assert len(setup.graph.queries) == 1
    assert len(setup.writer.written) == 1
    assert all(result["status"] == "ok" for result in (*results, third))
    assert third["reused"] is True


@pytest.mark.asyncio
async def test_the_same_text_on_two_routes_is_two_reads() -> None:
    setup = await _setup()

    await setup.tools.graph_query(TASK_ID, GraphQueryArguments(query="MATCH (n) RETURN count(*) AS c"))
    await setup.tools.timeseries_query(TASK_ID, TimeSeriesQueryArguments(query="MATCH (n) RETURN count(*) AS c"))

    assert len(setup.graph.queries) == 1 and len(setup.timeseries.queries) == 1


@pytest.mark.asyncio
async def test_a_failed_read_is_never_reused_and_its_reason_reaches_the_model() -> None:
    setup = await _setup()
    setup.graph.error = ValueError(f"GQL parse error near GROUP BY in graph {IDENTIFIER}")
    arguments = GraphQueryArguments(query="MATCH (r:Room) RETURN count(*) AS c GROUP BY r.name")

    failed = await setup.tools.graph_query(TASK_ID, arguments)
    setup.graph.error = None
    retried = await setup.tools.graph_query(TASK_ID, arguments)

    assert failed["status"] == "error"
    assert "GROUP BY" in failed["detail"] and IDENTIFIER not in failed["detail"]
    assert retried["status"] == "ok"
    assert len(setup.graph.queries) == 2
    assert len(setup.writer.written) == 1


@pytest.mark.asyncio
async def test_a_time_series_error_reaches_the_model_without_the_sources_address() -> None:
    setup = await _setup()
    setup.timeseries.error = ValueError("Failed to resolve table 'X' at https://trd-example.kusto.fabric.microsoft.com")

    result = await setup.tools.timeseries_query(TASK_ID, TimeSeriesQueryArguments(query="X | take 1"))

    assert "Failed to resolve table 'X'" in result["detail"]
    assert "kusto.fabric.microsoft.com" not in result["detail"]


@pytest.mark.asyncio
async def test_a_paused_capacity_is_explained_on_either_route() -> None:
    setup = await _setup()
    setup.graph.error = RuntimeError("CapacityNotActive: capacity is paused")
    setup.timeseries.error = RuntimeError("CapacityNotActive: capacity is paused")

    graph = await setup.tools.graph_query(TASK_ID, GraphQueryArguments(query="MATCH (n) RETURN count(*) AS c"))
    series = await setup.tools.timeseries_query(TASK_ID, TimeSeriesQueryArguments(query="T | count"))

    assert "capacity is paused" in graph["detail"] and "capacity is paused" in series["detail"]


@pytest.mark.asyncio
async def test_an_empty_answer_is_an_error_and_is_not_stored_as_evidence() -> None:
    setup = await _setup()
    setup.graph.result = _evidence("", row_count=None)

    result = await setup.tools.graph_query(TASK_ID, GraphQueryArguments(query="MATCH (n) RETURN count(*) AS c"))

    assert result == {"status": "error", "detail": "the Fabric query returned nothing to read"}
    assert setup.writer.written == []


@pytest.mark.asyncio
async def test_an_unlinked_user_is_asked_to_connect_fabric() -> None:
    setup = await _setup()
    setup.graph.error = FabricAuthorizationRequired("link Fabric")

    result = await setup.tools.graph_query(TASK_ID, GraphQueryArguments(query="MATCH (n) RETURN count(*) AS c"))

    assert result == {"status": "authorization_required", "detail": AUTHORIZATION_REQUIRED_DETAIL}
    [draft] = setup.events.drafts
    assert draft.type == "auth.required" and draft.payload == {"actionPath": "/api/fabric/auth/start"}
    assert draft.task_id == TASK_ID and draft.session_id == setup.task.session_id


@pytest.mark.asyncio
async def test_a_cancelled_task_reads_nothing() -> None:
    setup = await _setup()
    await setup.repository.append_command(setup.task.partition(), TASK_ID, CommandKind.CANCEL, None, "cancel-01")

    result = await setup.tools.graph_query(TASK_ID, GraphQueryArguments(query="MATCH (n:Room) RETURN count(*) AS c"))

    assert result["status"] == "cancelled"
    assert setup.graph.queries == []


@pytest.mark.asyncio
async def test_a_tool_call_without_its_trusted_task_scope_is_rejected() -> None:
    setup = await _setup()

    with pytest.raises(ValueError, match="trusted task scope"):
        await _tool(setup, "query_graph").func(Context(kwargs={}, metadata={}), query="MATCH (n) RETURN n")


@pytest.mark.asyncio
async def test_storage_failure_keeps_the_answer_and_warns_against_totals_from_a_preview() -> None:
    setup = await _setup(writer=Writer(failing=True))
    setup.graph.result = _evidence(ROWS * 3, preview=ROWS, row_count=6)

    result = await setup.tools.graph_query(TASK_ID, GraphQueryArguments(query="MATCH (r:Room) RETURN r"))

    assert result["status"] == "ok" and "evidence" not in result
    assert "Do not report totals over the preview" in result["evidenceNote"]


@pytest.mark.asyncio
async def test_the_eleventh_result_is_still_returned_with_its_reference() -> None:
    setup = await _setup()

    for index in range(11):
        result = await setup.tools.graph_query(
            TASK_ID, GraphQueryArguments(query=f"MATCH (r:Room) RETURN count(*) AS c LIMIT {index + 1}")
        )

    stored = await setup.repository.resolve_task(TASK_ID)
    assert stored is not None and len(stored.query_results) == 10
    assert "evidence" in result and "already lists ten" in result["evidenceNote"]


def test_task_coalescers_are_bounded_and_per_task() -> None:
    coalescers = TaskCoalescers(max_tasks=2)
    first = coalescers.for_task("task_aaaaaaaa")
    assert coalescers.for_task("task_aaaaaaaa") is first
    coalescers.for_task("task_bbbbbbbb")
    coalescers.for_task("task_cccccccc")

    assert coalescers.for_task("task_aaaaaaaa") is not first


def test_a_task_group_reports_what_actually_went_wrong() -> None:
    group = ExceptionGroup("unhandled errors in a TaskGroup", [ValueError("GQL parse error")])

    assert _failure_reason(group) == "GQL parse error"
    assert _status_detail(ExceptionGroup("outer", [group, RuntimeError("second")])) == "GQL parse error; second"


def test_no_reason_carries_an_identifier_or_link_out_to_the_reader() -> None:
    assert IDENTIFIER not in _status_detail(RuntimeError(f"workspace {IDENTIFIER} refused"))
    assert "https://" not in _status_detail(RuntimeError("endpoint https://example.test/x refused"))
    assert len(_status_detail(RuntimeError("x" * 1_000))) == 200


def test_source_text_is_redacted_to_names_and_values() -> None:
    redacted = source_reference_text(f"see https://x.test/{IDENTIFIER} or call getSchema and search_ontology")

    assert "https://" not in redacted and IDENTIFIER not in redacted
    assert "getSchema" not in redacted and "search_ontology" not in redacted
