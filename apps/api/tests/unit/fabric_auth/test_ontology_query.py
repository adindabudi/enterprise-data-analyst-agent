from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

import pytest
from eda_api.fabric_auth.ontology import (
    EXPECTED_ONTOLOGY_TOOLS,
    MAX_ONTOLOGY_RESULT_CHARS,
    MAX_ONTOLOGY_SCHEMA_CHARS,
    FabricOntologyQueryService,
    OntologyTarget,
    ontology_schema,
)
from eda_runtime_state.models import TaskPartition


def test_oversized_schema_keeps_all_names_without_optional_descriptions() -> None:
    payload = {"structuredContent": {"values": [
        {
            "name": "rooms",
            "semanticEnrichment": {"description": "x" * MAX_ONTOLOGY_SCHEMA_CHARS, "synonyms": ["wards"]},
            "properties": [
                {"name": "RoomId"},
                {"name": "RoomType", "semanticEnrichment": {"description": "single or shared"}},
            ],
            "timeseriesProperties": [{"name": "Occupancy"}],
        },
        {"name": "hospitals", "properties": [{"name": "HospitalName"}]},
    ]}}

    assert ontology_schema(payload) == "rooms: RoomId; RoomType; time series Occupancy | hospitals: HospitalName"


def test_schema_still_rejects_names_that_exceed_the_budget() -> None:
    payload = {"structuredContent": {"values": [
        {"name": "rooms", "properties": [{"name": "RoomProperty" + str(index)} for index in range(300)]},
    ]}}

    with pytest.raises(ValueError, match="description budget"):
        ontology_schema(payload)


def _target() -> OntologyTarget:
    return OntologyTarget.model_validate(
        {
            "workspaceId": "44444444-4444-4444-4444-444444444444",
            "ontologyId": "55555555-5555-5555-5555-555555555555",
            "description": "Lamna healthcare operations ontology",
            "routingTerms": ["patients", "pasien", "admissions", "equipment"],
        }
    )


def _partition() -> TaskPartition:
    return TaskPartition(
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
        session_id="ses_interactive_12345678",
    )


class TokenProvider:
    def __init__(self) -> None:
        self.calls: list[TaskPartition] = []

    async def acquire(self, partition: TaskPartition) -> str:
        self.calls.append(partition)
        return "linked-user-token"


class Session:
    def __init__(self, *, drifted: bool = False) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.drifted = drifted

    async def initialize(self) -> None:
        self.calls.append(("initialize", None))

    async def list_tools(self) -> list[dict[str, object]]:
        self.calls.append(("tools/list", None))
        tools = [
            {"name": name, "description": definition["description"], "inputSchema": definition["inputSchema"]}
            for name, definition in EXPECTED_ONTOLOGY_TOOLS.items()
        ]
        return tools[:-1] if self.drifted else tools

    async def call_tool(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        self.calls.append((name, arguments))
        return {
            "structuredContent": {
                "raw": {"Fields": ["patient_count"], "Value": [[5]]},
                "naturalLanguageResponse": "Jumlah pasien adalah 5.",
            },
            "isError": False,
        }


@pytest.mark.asyncio
async def test_gateway_streams_real_stages_and_returns_provider_answer_without_model() -> None:
    token_provider = TokenProvider()
    session = Session()

    @asynccontextmanager
    async def open_session(url: str, bearer_token: str):
        assert url.endswith("/55555555-5555-5555-5555-555555555555/ontologyEndpoint")
        assert bearer_token == "linked-user-token"  # noqa: S105 - synthetic test value
        yield session

    gateway = FabricOntologyQueryService(
        {"lamna-healthcare": _target()},
        token_provider=token_provider,
        session_opener=open_session,
    )

    updates = [update async for update in gateway.stream(_partition(), "Ada berapa jumlah pasien?")]

    assert [update.status for update in updates[:-1]] == [
        "Checking Fabric connection",
        "Connecting to Lamna healthcare operations ontology",
        "Validating ontology tools",
        "Querying Lamna healthcare operations ontology",
    ]
    assert updates[-1].result == '{"Fields":["patient_count"],"Value":[[5]]}'
    assert token_provider.calls == [_partition()]
    assert session.calls[:2] == [("initialize", None), ("tools/list", None)]
    tool_name, arguments = session.calls[-1]
    assert tool_name == "search_ontology"
    assert arguments == {"naturalLanguageQuery": "Ada berapa jumlah pasien?", "naturalLanguageResponse": False}


@pytest.mark.asyncio
async def test_gateway_fails_closed_before_search_when_tool_contract_drifts() -> None:
    session = Session(drifted=True)

    @asynccontextmanager
    async def open_session(url: str, bearer_token: str):
        del url, bearer_token
        yield session

    gateway = FabricOntologyQueryService(
        {"lamna-healthcare": _target()},
        token_provider=TokenProvider(),
        session_opener=open_session,
    )

    with pytest.raises(ValueError, match="tool contract"):
        async for _ in gateway.stream(_partition(), "Jumlah pasien"):
            pass

    assert all(name != "search_ontology" for name, _ in session.calls)


@pytest.mark.asyncio
async def test_gateway_returns_raw_rows_and_drops_the_unreliable_summary() -> None:
    session = Session()

    @asynccontextmanager
    async def open_session(url: str, bearer_token: str):
        del url, bearer_token
        yield session

    gateway = FabricOntologyQueryService(
        {"lamna-healthcare": _target()},
        token_provider=TokenProvider(),
        session_opener=open_session,
    )

    question = "How many patients are still admitted right now?"
    updates = [update async for update in gateway.stream(_partition(), question)]

    _, arguments = session.calls[-1]
    assert arguments == {"naturalLanguageQuery": question, "naturalLanguageResponse": False}
    result = updates[-1].result
    assert result is not None
    assert json.loads(result) == {"Fields": ["patient_count"], "Value": [[5]]}
    assert "Jumlah pasien adalah 5." not in result


@pytest.mark.asyncio
async def test_gateway_truncates_an_oversized_result_and_reports_the_total() -> None:
    class WideSession(Session):
        async def call_tool(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
            self.calls.append((name, arguments))
            return {
                "structuredContent": {
                    "raw": {
                        "Fields": ["PatientId", "Note"],
                        "Value": [[index, "x" * 200] for index in range(200)],
                    }
                },
                "isError": False,
            }

    session = WideSession()

    @asynccontextmanager
    async def open_session(url: str, bearer_token: str):
        del url, bearer_token
        yield session

    gateway = FabricOntologyQueryService(
        {"lamna-healthcare": _target()},
        token_provider=TokenProvider(),
        session_opener=open_session,
    )

    updates = [update async for update in gateway.stream(_partition(), "List every patient note")]

    result = updates[-1].result
    assert result is not None
    assert len(result) <= MAX_ONTOLOGY_RESULT_CHARS
    payload = json.loads(result)
    assert payload["truncated"] is True
    assert payload["totalRows"] == 200
    assert 0 < payload["returnedRows"] < 200
    assert payload["Fields"] == ["PatientId", "Note"]


def test_gateway_exposes_its_single_source_identity_for_tool_binding() -> None:
    gateway = FabricOntologyQueryService({"lamna-healthcare": _target()}, token_provider=TokenProvider())

    assert gateway.alias == "lamna-healthcare"
    assert gateway.description == "Lamna healthcare operations ontology"


@pytest.mark.asyncio
async def test_gateway_drops_node_blob_columns_so_every_row_survives() -> None:
    # The ontology repeats each node as a *_json blob, which used to consume the
    # size budget and silently truncate 24 rows down to 6.
    class BlobSession(Session):
        async def call_tool(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
            self.calls.append((name, arguments))
            rows = [
                [index, f"Patient {index}", "Chronic Low Oxygen", json.dumps({"properties": {"pad": "x" * 400}})]
                for index in range(24)
            ]
            return {
                "structuredContent": {
                    "raw": {
                        "Fields": ["PatientId", "PatientName", "ClinicalStatus", "summary_json"],
                        "Value": rows,
                    }
                },
                "isError": False,
            }

    session = BlobSession()

    @asynccontextmanager
    async def open_session(url: str, bearer_token: str):
        del url, bearer_token
        yield session

    gateway = FabricOntologyQueryService(
        {"lamna-healthcare": _target()},
        token_provider=TokenProvider(),
        session_opener=open_session,
    )

    updates = [update async for update in gateway.stream(_partition(), "Which patients have chronic low oxygen?")]

    result = updates[-1].result
    assert result is not None
    payload = json.loads(result)
    assert payload["Fields"] == ["PatientId", "PatientName", "ClinicalStatus"]
    assert len(payload["Value"]) == 24
    assert "truncated" not in payload
    assert "summary_json" not in result


@pytest.mark.asyncio
async def test_the_tool_contract_is_confirmed_once_not_on_every_query() -> None:
    session = Session()

    @asynccontextmanager
    async def open_session(url: str, bearer_token: str):
        del url, bearer_token
        yield session

    gateway = FabricOntologyQueryService(
        {"lamna-healthcare": _target()},
        token_provider=TokenProvider(),
        session_opener=open_session,
    )

    for _ in range(3):
        async for _update in gateway.stream(_partition(), "Ada berapa jumlah pasien?"):
            pass

    assert [name for name, _ in session.calls].count("tools/list") == 1
    assert [name for name, _ in session.calls].count("search_ontology") == 3


@pytest.mark.asyncio
async def test_a_drifted_tool_contract_still_fails_the_first_query() -> None:
    session = Session(drifted=True)

    @asynccontextmanager
    async def open_session(url: str, bearer_token: str):
        del url, bearer_token
        yield session

    gateway = FabricOntologyQueryService(
        {"lamna-healthcare": _target()},
        token_provider=TokenProvider(),
        session_opener=open_session,
    )

    with pytest.raises(ValueError):
        async for _update in gateway.stream(_partition(), "Ada berapa jumlah pasien?"):
            pass


@pytest.mark.asyncio
async def test_schema_carries_the_synonyms_and_stored_values_the_ontology_holds() -> None:
    class SchemaSession(Session):
        async def call_tool(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
            self.calls.append((name, arguments))
            return {
                "isError": False,
                "structuredContent": {
                    "values": [
                        {
                            "name": "departments",
                            "semanticEnrichment": {
                                "synonyms": ["units", "wards", "ICU"],
                                "description": "A clinical unit inside a hospital.",
                            },
                            "properties": [
                                {"name": "DepartmentId"},
                                {
                                    "name": "DepartmentName",
                                    "semanticEnrichment": {
                                        "description": "The exact values present are: Intensive Care Unit.",
                                    },
                                },
                            ],
                        }
                    ]
                },
            }

    session = SchemaSession()

    @asynccontextmanager
    async def open_session(url: str, bearer_token: str):
        del url, bearer_token
        yield session

    gateway = FabricOntologyQueryService(
        {"lamna-healthcare": _target()},
        token_provider=TokenProvider(),
        session_opener=open_session,
    )

    schema = await gateway.schema(_partition())

    assert "also: units, wards, ICU" in schema
    assert "The exact values present are: Intensive Care Unit." in schema
    assert "A clinical unit inside a hospital." in schema
    assert await gateway.schema(_partition()) is schema
