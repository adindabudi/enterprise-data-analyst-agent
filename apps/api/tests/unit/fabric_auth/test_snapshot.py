"""The schema snapshot: built once from the ontology listing and GQL introspection, pinned as instructions."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from azure.cosmos.exceptions import CosmosResourceNotFoundError
from eda_api.fabric_auth.snapshot import (
    MAX_STORED_VALUES,
    SchemaSnapshot,
    SnapshotUnavailableError,
    build_snapshot,
    read_snapshot,
    relationships_from_rows,
    render_snapshot,
    snapshot_id,
    stored_values_from_rows,
    stored_values_query,
    value_candidates,
)
from eda_api.fabric_ontology import OntologyTarget

WORKSPACE = "b82afbde-8304-44c0-ac94-3cf69f6da909"
ONTOLOGY = "5566b159-6998-4bb8-a167-6d5bf3c43352"
GRAPH_MODEL = "bdf1d01e-7da8-4b58-873a-54d8edb12f34"
TARGET = OntologyTarget.model_validate(
    {
        "workspaceId": WORKSPACE,
        "ontologyId": ONTOLOGY,
        "graphModelId": GRAPH_MODEL,
        "kqlDatabaseId": "7b310305-c7c9-4e02-84f0-12f36607fa22",
        "description": "Synthetic hospital operations",
    }
)

# Trimmed from what list_ontology_entity_types returned for the lab ontology on 2026-10-01.
LISTING: list[dict[str, object]] = [
    {
        "name": "vitalsignequipment",
        "entityIdParts": ["71"],
        "properties": [
            {"id": "71", "name": "EquipmentId", "valueType": "String"},
            {
                "id": "72",
                "name": "PatientId",
                "valueType": "BigInt",
                "semanticEnrichment": {"description": "The monitored patient; joins patients.PatientId."},
            },
            {"id": "74", "name": "EquipmentType", "valueType": "String"},
        ],
        "timeseriesProperties": [
            {"id": "81", "name": "HeartRate", "valueType": "BigInt"},
            {"id": "82", "name": "OxygenSaturation", "valueType": "BigInt"},
            {"id": "83", "name": "Timestamp", "valueType": "DateTime"},
        ],
        "mappings": [
            {
                "mappingConfiguration": {
                    "mappingType": "NonTimeSeries",
                    "sourceTableProperties": {"sourceType": "LakehouseTable", "sourceTableName": "vitalsignequipment"},
                    "propertyBindings": [],
                }
            },
            {
                "mappingConfiguration": {
                    "mappingType": "TimeSeries",
                    "timestampColumnName": "Timestamp",
                    "sourceTableProperties": {
                        "sourceType": "KustoTable",
                        "clusterUri": "https://trd-example.z8.kusto.fabric.microsoft.com",
                        "databaseName": "LamnaHealthcareEH",
                        "sourceTableName": "VitalSignsReadings",
                    },
                    "propertyBindings": [
                        {"sourceColumnName": "EquipmentId", "targetPropertyId": "71"},
                        {"sourceColumnName": "HeartRate", "targetPropertyId": "81"},
                        {"sourceColumnName": "SpO2", "targetPropertyId": "82"},
                        {"sourceColumnName": "Timestamp", "targetPropertyId": "83"},
                    ],
                }
            },
        ],
        "semanticEnrichment": {"synonyms": ["monitor", "bedside monitor"], "description": "Bedside monitors."},
    },
    {
        "name": "hospitals",
        "entityIdParts": ["11"],
        "properties": [
            {"id": "11", "name": "HospitalId", "valueType": "BigInt"},
            {"id": "12", "name": "HospitalName", "valueType": "String"},
        ],
        "timeseriesProperties": [],
        "mappings": [],
    },
]
RELATIONSHIP_ROWS: list[object] = [
    {"rel": ["vitalsignequipment_has_patients"], "src": ["vitalsignequipment"], "dst": ["patients"], "n": 60},
    {"rel": ["departments_has_hospitals"], "src": ["departments"], "dst": ["hospitals"], "n": 20},
    {"rel": ["departments_has_hospitals"], "src": ["departments"], "dst": ["hospitals"], "n": 20},
    {"rel": [], "src": ["rooms"], "dst": []},
]
HOSPITALS = ("Lamna Healthcare Academic Medical Center", "Lamna Healthcare Cascade General")


def _snapshot() -> SchemaSnapshot:
    return build_snapshot(
        alias="lamna-healthcare",
        target=TARGET,
        graph_model_id=UUID(GRAPH_MODEL),
        listing=LISTING,
        relationship_rows=RELATIONSHIP_ROWS,
        stored_values={("hospitals", "HospitalName"): HOSPITALS, ("vitalsignequipment", "EquipmentId"): None},
        generated_at=datetime(2026, 10, 1, tzinfo=UTC),
    )


def test_the_snapshot_keeps_entities_keys_and_the_values_actually_stored() -> None:
    snapshot = _snapshot()

    hospitals = next(entity for entity in snapshot.entities if entity.name == "hospitals")
    name = next(prop for prop in hospitals.properties if prop.name == "HospitalName")
    key = next(prop for prop in hospitals.properties if prop.name == "HospitalId")
    assert name.stored_values == HOSPITALS and key.key is True
    assert [entity.name for entity in snapshot.entities] == ["hospitals", "vitalsignequipment"]


def test_a_time_series_binding_names_its_table_time_column_values_and_join_key() -> None:
    equipment = next(entity for entity in _snapshot().entities if entity.name == "vitalsignequipment")

    [series] = equipment.time_series
    assert series.table == "VitalSignsReadings" and series.timestamp_column == "Timestamp"
    assert [(column.column, column.property_name) for column in series.key_columns] == [("EquipmentId", "EquipmentId")]
    # A column can carry a different name from its property; the query needs the column.
    assert [(column.column, column.property_name) for column in series.value_columns] == [
        ("HeartRate", "HeartRate"),
        ("SpO2", "OxygenSaturation"),
    ]


def test_relationships_keep_their_direction_and_drop_edges_that_could_not_be_named() -> None:
    edges = relationships_from_rows(RELATIONSHIP_ROWS)

    # A half-named edge reads as a real one, and the model would then write a traversal that cannot run.
    assert [(edge.source, edge.name, edge.target, edge.count) for edge in edges] == [
        ("departments", "departments_has_hospitals", "hospitals", 20),
        ("vitalsignequipment", "vitalsignequipment_has_patients", "patients", 60),
    ]


def test_the_rendered_snapshot_is_what_the_agent_needs_and_nothing_that_locates_the_source() -> None:
    text = render_snapshot(_snapshot(), description=TARGET.description, timeseries=True)

    assert (
        "HospitalName String = 'Lamna Healthcare Academic Medical Center' | 'Lamna Healthcare Cascade General'" in text
    )
    assert "(:departments)-[:departments_has_hospitals]->(:hospitals)" in text
    assert "Eventhouse table VitalSignsReadings, time column Timestamp" in text
    assert "join VitalSignsReadings.EquipmentId = vitalsignequipment.EquipmentId" in text
    assert "query_timeseries" in text
    for locator in (WORKSPACE, ONTOLOGY, GRAPH_MODEL, "kusto.fabric.microsoft.com", "LamnaHealthcareEH"):
        assert locator not in text


def test_without_a_kql_database_the_agent_is_told_time_series_are_unreadable() -> None:
    text = render_snapshot(_snapshot(), description=TARGET.description, timeseries=False)

    assert "query_timeseries" not in text
    assert "cannot be read in this deployment" in text


def test_the_snapshot_survives_a_round_trip_through_its_published_document() -> None:
    snapshot = _snapshot()
    document = snapshot.model_dump(mode="json", by_alias=True)

    assert document["id"] == snapshot_id("lamna-healthcare") == "fabric-schema-snapshot:lamna-healthcare"
    assert document["recordType"] == "fabricSchemaSnapshot"
    assert SchemaSnapshot.model_validate(document) == snapshot


def test_stored_values_are_listed_only_when_every_value_came_back() -> None:
    few = [{"v": "Telemetry", "c": 4}, {"v": "Intensive Care Unit", "c": 9}, {"v": None, "c": 1}]
    many = [{"v": f"value {index}", "c": 1} for index in range(MAX_STORED_VALUES + 1)]

    assert stored_values_from_rows(few) == ("Intensive Care Unit", "Telemetry")
    assert stored_values_from_rows(many) is None
    assert stored_values_from_rows([{"v": "line\nbreak", "c": 1}]) is None


def test_the_values_query_asks_for_one_row_more_than_it_will_list() -> None:
    query = stored_values_query("hospitals", "HospitalName")

    assert query == (
        "MATCH (n:hospitals) LET v = n.HospitalName RETURN v, count(*) AS c "
        f"GROUP BY v ORDER BY c DESC LIMIT {MAX_STORED_VALUES + 1}"
    )
    # A name that is not a plain identifier is never written into a query.
    assert stored_values_query("hospitals", "Hospital-Name") is None


def test_value_candidates_are_the_string_properties_of_graph_entities() -> None:
    assert value_candidates(LISTING) == [
        ("vitalsignequipment", "EquipmentId"),
        ("vitalsignequipment", "EquipmentType"),
        ("hospitals", "HospitalName"),
    ]


class Container:
    def __init__(self, document: dict[str, Any] | None) -> None:
        self.document = document
        self.reads: list[tuple[str, str]] = []

    async def read_item(self, item: str, partition_key: str) -> dict[str, Any]:
        self.reads.append((item, partition_key))
        if self.document is None:
            raise CosmosResourceNotFoundError(message="missing")
        return self.document


@pytest.mark.asyncio
async def test_the_published_snapshot_is_read_for_the_configured_source() -> None:
    document = {**_snapshot().model_dump(mode="json", by_alias=True), "_etag": "x", "_ts": 1}
    container = Container(document)

    snapshot = await read_snapshot(container, "lamna-healthcare", TARGET)

    assert snapshot == _snapshot()
    assert container.reads == [("fabric-schema-snapshot:lamna-healthcare", "fabric-schema-snapshot:lamna-healthcare")]


@pytest.mark.asyncio
async def test_a_missing_snapshot_says_how_to_publish_one() -> None:
    with pytest.raises(SnapshotUnavailableError, match="build-fabric-schema-snapshot"):
        await read_snapshot(Container(None), "lamna-healthcare", TARGET)


@pytest.mark.asyncio
async def test_another_sources_snapshot_is_refused() -> None:
    document = _snapshot().model_dump(mode="json", by_alias=True)
    other = TARGET.model_copy(update={"ontology_id": UUID(int=9)})

    with pytest.raises(SnapshotUnavailableError, match="different source"):
        await read_snapshot(Container(document), "lamna-healthcare", other)


@pytest.mark.asyncio
async def test_a_malformed_snapshot_is_refused_rather_than_partly_used() -> None:
    document = {**_snapshot().model_dump(mode="json", by_alias=True), "entities": []}

    with pytest.raises(SnapshotUnavailableError, match="invalid"):
        await read_snapshot(Container(document), "lamna-healthcare", TARGET)
