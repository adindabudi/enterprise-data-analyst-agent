"""Time series through the KQL database's remote MCP server: the query the model wrote, bounded and read-only."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

import pytest
from eda_api.fabric_auth.capacity import CapacityMonitor, CapacityState
from eda_api.fabric_auth.eventhouse import (
    EXECUTE_TOOL,
    KQL_MAX_RECORDS,
    FabricEventhouseQueryService,
    KqlQueryError,
    kql_failure_detail,
    validate_kql_statement,
)
from eda_api.fabric_ontology import OntologyTarget
from eda_runtime_state.models import TaskPartition

WORKSPACE = "b82afbde-8304-44c0-ac94-3cf69f6da909"
KQL_DATABASE = "7b310305-c7c9-4e02-84f0-12f36607fa22"
PARTITION = TaskPartition(
    tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
    owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
    session_id="ses_interactive_12345678",
)
TARGET = OntologyTarget.model_validate(
    {
        "workspaceId": WORKSPACE,
        "ontologyId": "5566b159-6998-4bb8-a167-6d5bf3c43352",
        "kqlDatabaseId": KQL_DATABASE,
        "description": "Lamna healthcare operations ontology",
    }
)
# The executeQuery tool as the KQL database's remote MCP server listed it on 2026-10-01.
EXECUTE_SCHEMA = {
    "type": "object",
    "properties": {
        "kqlQuery": {"type": "string", "description": "kql query"},
        "maxRecords": {"type": "number"},
        "activityTitle": {"type": "string"},
        "activityDescription": {"type": "string"},
        "clusterUrl": {"type": "string"},
        "databaseName": {"type": "string"},
    },
    "required": ["kqlQuery", "maxRecords"],
}


def v1_result(columns: list[str], rows: list[list[object]]) -> dict[str, object]:
    document = {
        "Tables": [
            {"TableName": "QueryProperties", "Columns": [{"ColumnName": "Key"}], "Rows": [["Visualization"]]},
            {"TableName": "PrimaryResult", "Columns": [{"ColumnName": name} for name in columns], "Rows": rows},
            {"TableName": "QueryCompletionInformation", "Columns": [{"ColumnName": "RowCount"}], "Rows": [[2]]},
        ]
    }
    return {"isError": False, "content": [{"type": "text", "text": json.dumps(document)}]}


class Token:
    def __init__(self, value: str = "owner-token") -> None:
        self.value = value
        self.calls: list[TaskPartition] = []

    async def acquire(self, partition: TaskPartition) -> str:
        self.calls.append(partition)
        return self.value


class Session:
    def __init__(self, answer: object, *, schema: dict[str, object] | None = None) -> None:
        self.answer = answer
        self.schema = schema or EXECUTE_SCHEMA
        self.calls: list[tuple[str, Any]] = []
        self.gate: asyncio.Event | None = None

    async def initialize(self) -> None:
        self.calls.append(("initialize", None))

    async def list_tools(self) -> list[dict[str, object]]:
        self.calls.append(("tools/list", None))
        return [
            {"name": "getSchema", "inputSchema": {"type": "object", "properties": {}}},
            {"name": EXECUTE_TOOL, "inputSchema": self.schema},
        ]

    async def call_tool(self, name: str, arguments: dict[str, object]) -> object:
        self.calls.append((name, arguments))
        if self.gate is not None:
            await self.gate.wait()
        return self.answer


def service(
    session: Session, *, monitor: CapacityMonitor | None = None, token: Token | None = None, deadline: float = 120.0
) -> tuple[FabricEventhouseQueryService, list[tuple[str, str]]]:
    opened: list[tuple[str, str]] = []

    @asynccontextmanager
    async def open_session(url: str, bearer_token: str):
        opened.append((url, bearer_token))
        yield session

    created = FabricEventhouseQueryService(
        TARGET,
        token_provider=token or Token(),
        session_opener=open_session,
        capacity=monitor,
        deadline_seconds=deadline,
    )
    CREATED.append(created)
    return created, opened


CREATED: list[FabricEventhouseQueryService] = []


@pytest.fixture(autouse=True)
async def close_services():
    yield
    while CREATED:
        await CREATED.pop().aclose()


@pytest.mark.asyncio
async def test_the_query_the_model_wrote_reaches_the_configured_database_with_the_users_token() -> None:
    session = Session(v1_result(["EquipmentId", "maxHr"], [["VS-1020", 126], ["VS-1038", 125]]))
    kql, opened = service(session)
    query = "  VitalSignsReadings | summarize maxHr = max(HeartRate) by EquipmentId | top 2 by maxHr  "

    evidence = await kql.execute_evidence(PARTITION, query)

    assert opened == [
        (
            f"https://api.fabric.microsoft.com/v1/mcp/dataPlane/workspaces/{WORKSPACE}"
            f"/items/{KQL_DATABASE}/kqlEndpoint",
            "owner-token",
        )
    ]
    assert session.calls[-1] == (EXECUTE_TOOL, {"kqlQuery": query.strip(), "maxRecords": KQL_MAX_RECORDS})
    assert json.loads(evidence.complete) == [
        {"EquipmentId": "VS-1020", "maxHr": 126},
        {"EquipmentId": "VS-1038", "maxHr": 125},
    ]
    assert evidence.row_count == 2 and evidence.source_incomplete is False


@pytest.mark.asyncio
async def test_no_cluster_or_database_override_is_ever_sent() -> None:
    session = Session(v1_result(["n"], [[1]]))
    kql, _ = service(session)

    await kql.execute(PARTITION, "VitalSignsReadings | count")

    # The endpoint would run the query on any cluster named here; the tool never names one.
    assert set(session.calls[-1][1]) == {"kqlQuery", "maxRecords"}


@pytest.mark.asyncio
async def test_a_result_at_the_record_cap_is_marked_incomplete() -> None:
    session = Session(v1_result(["n"], [[index] for index in range(KQL_MAX_RECORDS)]))
    kql, _ = service(session)

    evidence = await kql.execute_evidence(PARTITION, "VitalSignsReadings | project n = HeartRate")

    assert evidence.source_incomplete is True
    assert evidence.preview_truncated is True and evidence.row_count == KQL_MAX_RECORDS


@pytest.mark.asyncio
async def test_the_tool_contract_is_confirmed_once_not_before_every_query() -> None:
    session = Session(v1_result(["n"], [[1]]))
    kql, _ = service(session)

    await kql.execute(PARTITION, "VitalSignsReadings | count")
    await kql.execute(PARTITION, "VitalSignsReadings | take 1")

    assert [name for name, _ in session.calls].count("tools/list") == 1


@pytest.mark.asyncio
async def test_a_changed_contract_sends_no_query() -> None:
    changed = {**EXECUTE_SCHEMA, "required": ["kqlQuery", "maxRecords", "databaseName"]}
    session = Session(v1_result(["n"], [[1]]), schema=changed)
    kql, _ = service(session)

    with pytest.raises(KqlQueryError, match="contract changed"):
        await kql.execute(PARTITION, "VitalSignsReadings | count")

    assert EXECUTE_TOOL not in [name for name, _ in session.calls]


PROVIDER_ERROR = (
    "Error in executing KQL query. cluster='https://trd-example.z8.kusto.fabric.microsoft.com', "
    'database=\'LamnaHealthcareEH\', Exception=\'Semantic error: {\n    "error": {\n        "code": '
    '"General_BadRequest",\n        "@message": "Semantic error: \'take\' operator: Failed to resolve table '
    'or column expression named \'NoSuchTable\'",\n        "@context": {"clientRequestId": '
    '"KustoCopilot;fc7df1f0-37f8-4e3b-b56d-33315b3bed4c"}}}\''
)


@pytest.mark.asyncio
async def test_a_rejected_query_returns_the_engines_reason_without_the_sources_address() -> None:
    session = Session({"isError": True, "content": [{"type": "text", "text": PROVIDER_ERROR}]})
    kql, _ = service(session)

    with pytest.raises(KqlQueryError) as failed:
        await kql.execute(PARTITION, "NoSuchTable | take 1")

    reason = str(failed.value)
    assert "Failed to resolve table or column expression named 'NoSuchTable'" in reason
    assert "kusto.fabric.microsoft.com" not in reason and "LamnaHealthcareEH" not in reason
    assert "fc7df1f0" not in reason and len(reason) <= 200


def test_a_paused_capacity_is_named_as_such() -> None:
    assert kql_failure_detail("Internal error CapacityNotActive.Capacity x is not active").startswith(
        "CapacityNotActive"
    )


@pytest.mark.asyncio
async def test_a_paused_capacity_refusal_records_the_pause() -> None:
    monitor = CapacityMonitor()
    monitor.record(CapacityState.ACTIVE)
    refused = {"isError": True, "content": [{"type": "text", "text": "CapacityNotActive: capacity is paused"}]}
    kql, _ = service(Session(refused), monitor=monitor)

    with pytest.raises(KqlQueryError):
        await kql.execute(PARTITION, "VitalSignsReadings | count")

    assert monitor.fresh() is CapacityState.PAUSED


@pytest.mark.asyncio
async def test_rows_that_came_back_are_a_running_capacity() -> None:
    monitor = CapacityMonitor()
    monitor.record(CapacityState.PAUSED)
    kql, _ = service(Session(v1_result(["n"], [[1]])), monitor=monitor)

    await kql.execute(PARTITION, "VitalSignsReadings | count")

    assert monitor.fresh() is CapacityState.ACTIVE


@pytest.mark.asyncio
async def test_a_query_that_outlives_its_deadline_is_an_unknown_outcome_not_an_empty_one() -> None:
    session = Session(v1_result(["n"], [[1]]))
    session.gate = asyncio.Event()
    kql, _ = service(session, deadline=0.01)

    with pytest.raises(KqlQueryError, match="did not finish"):
        await kql.execute(PARTITION, "VitalSignsReadings | count")


@pytest.mark.asyncio
async def test_no_token_means_no_call() -> None:
    session = Session(v1_result(["n"], [[1]]))
    kql, opened = service(session, token=Token(""))

    with pytest.raises(KqlQueryError, match="token"):
        await kql.execute(PARTITION, "VitalSignsReadings | count")

    assert opened == []


@pytest.mark.parametrize(
    "query",
    [
        ".drop table VitalSignsReadings",
        "  .show tables",
        "VitalSignsReadings | take 1;\n.set-or-append T <| VitalSignsReadings",
        "let x = 1;\n.drop table T",
        "cluster('https://other.kusto.windows.net').database('db').T | take 1",
        "database('Other').Secrets | take 1",
        "externaldata (a: string) [h@'https://example/blob.csv'] | take 1",
        "external_table('Archive') | count",
        "evaluate http_request('https://example')",
        "range x from 1 to 1 step 1 | evaluate python(typeof(*), 'result = df')",
        "sql_request('Server=x', 'select 1')",
        "x",
    ],
)
def test_statements_that_leave_the_database_or_change_it_are_refused(query: str) -> None:
    with pytest.raises(KqlQueryError):
        validate_kql_statement(query)


@pytest.mark.parametrize(
    "query",
    [
        "VitalSignsReadings | summarize arg_max(Timestamp, HeartRate) by EquipmentId",
        "VitalSignsReadings\n| where Timestamp > ago(1d)\n| summarize avg(HeartRate) by bin(Timestamp, 1h)",
        # A literal may say anything; only the statement's own text is checked.
        "VitalSignsReadings | where Note == '.drop table T' or Note == \"cluster('x')\" | count",
        "let peak = toscalar(VitalSignsReadings | summarize max(HeartRate)); VitalSignsReadings | where HeartRate == peak",
    ],
)
def test_ordinary_read_queries_pass_unchanged(query: str) -> None:
    assert validate_kql_statement(query) == query.strip()


def test_a_target_without_a_kql_database_has_no_time_series_service() -> None:
    without = TARGET.model_copy(update={"kql_database_id": None})

    with pytest.raises(ValueError, match="no KQL database"):
        FabricEventhouseQueryService(without, token_provider=Token())


def test_the_kql_endpoint_follows_the_workspace_private_link_host() -> None:
    private = OntologyTarget.model_validate({**TARGET.model_dump(by_alias=True, mode="json"), "privateLinkZone": "z12"})

    assert private.kql_endpoint == (
        f"https://{UUID(WORKSPACE).hex}.z12.w.api.fabric.microsoft.com/v1/mcp/dataPlane/workspaces/{WORKSPACE}"
        f"/items/{KQL_DATABASE}/kqlEndpoint"
    )


def test_the_kql_database_must_be_its_own_item() -> None:
    with pytest.raises(ValueError, match="KQL database ID must differ"):
        OntologyTarget.model_validate({**TARGET.model_dump(by_alias=True, mode="json"), "kqlDatabaseId": WORKSPACE})
