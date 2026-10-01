"""What the application still asks of the ontology endpoint: a capacity handshake, and the snapshot job's listing."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any

import pytest
from eda_api.fabric_auth.capacity import CapacityMonitor, CapacityState
from eda_api.fabric_auth.ontology import (
    LIST_ENTITY_TYPES_TOOL,
    MAX_ONTOLOGY_SCHEMA_CHARS,
    OntologyEndpointProbe,
    list_entity_types,
    ontology_schema,
)
from eda_api.fabric_ontology import OntologyTarget


def test_oversized_schema_keeps_all_names_without_optional_descriptions() -> None:
    payload = {
        "structuredContent": {
            "values": [
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
            ]
        }
    }

    assert ontology_schema(payload) == "rooms: RoomId; RoomType; time series Occupancy | hospitals: HospitalName"


def test_schema_still_rejects_names_that_exceed_the_budget() -> None:
    payload = {
        "structuredContent": {
            "values": [
                {
                    "name": "rooms",
                    "properties": [
                        {"name": "RoomProperty" + str(index)} for index in range(MAX_ONTOLOGY_SCHEMA_CHARS // 10)
                    ],
                },
            ]
        }
    }

    with pytest.raises(ValueError, match="description budget"):
        ontology_schema(payload)


def _target() -> OntologyTarget:
    return OntologyTarget.model_validate(
        {
            "workspaceId": "44444444-4444-4444-4444-444444444444",
            "ontologyId": "55555555-5555-5555-5555-555555555555",
            "description": "Lamna healthcare operations ontology",
        }
    )


class Session:
    def __init__(self, listing: object | None = None) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.listing = listing

    async def initialize(self) -> None:
        self.calls.append(("initialize", None))

    async def list_tools(self) -> list[object]:
        self.calls.append(("tools/list", None))
        return []

    async def call_tool(self, name: str, arguments: dict[str, object]) -> object:
        self.calls.append((name, arguments))
        return self.listing


class PausedSession(Session):
    """The handshake a paused capacity gives: the endpoint answers, and refuses to start."""

    async def initialize(self) -> None:
        self.calls.append(("initialize", None))
        raise ExceptionGroup(
            "unhandled errors in a TaskGroup",
            [RuntimeError("Internal error CapacityNotActive.Capacity [id] is not active")],
        )


def _probe(session: Session, monitor: CapacityMonitor) -> tuple[OntologyEndpointProbe, list[str]]:
    opened: list[str] = []

    @asynccontextmanager
    async def open_session(url: str, bearer_token: str):
        del bearer_token
        opened.append(url)
        yield session

    return OntologyEndpointProbe(_target(), session_opener=open_session, capacity=monitor), opened


@pytest.mark.asyncio
async def test_the_capacity_probe_opens_the_endpoint_and_runs_nothing() -> None:
    monitor = CapacityMonitor()
    session = Session()
    probe, opened = _probe(session, monitor)

    await probe.probe_capacity("linked-user-token")

    assert opened == [_target().endpoint]
    assert session.calls == [("initialize", None)]
    assert monitor.fresh() is CapacityState.ACTIVE


@pytest.mark.asyncio
async def test_a_handshake_refused_for_a_paused_capacity_records_the_pause() -> None:
    monitor = CapacityMonitor()
    probe, _ = _probe(PausedSession(), monitor)

    with pytest.raises(ExceptionGroup):
        await probe.probe_capacity("linked-user-token")

    assert monitor.fresh() is CapacityState.PAUSED


@pytest.mark.asyncio
async def test_the_listing_asks_for_properties_and_returns_every_entity() -> None:
    values = [{"name": "rooms", "properties": [{"name": "RoomId"}]}, {"name": "hospitals", "properties": []}]
    session = Session({"isError": False, "structuredContent": {"values": values}})

    listed = await list_entity_types(session)

    assert listed == values
    assert session.calls == [(LIST_ENTITY_TYPES_TOOL, {"includeProperties": True})]


@pytest.mark.asyncio
async def test_the_listing_reads_a_text_only_answer() -> None:
    values = [{"name": "rooms", "properties": []}]
    session = Session({"isError": False, "content": [{"type": "text", "text": json.dumps({"values": values})}]})

    assert await list_entity_types(session) == values


@pytest.mark.asyncio
async def test_a_failed_listing_is_never_an_empty_schema() -> None:
    session = Session({"isError": True, "content": [{"type": "text", "text": "forbidden"}]})

    with pytest.raises(ValueError, match="returned an error"):
        await list_entity_types(session)
