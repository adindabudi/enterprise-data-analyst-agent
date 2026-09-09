from __future__ import annotations

import json

import pytest
from eda_worker.fabric.ontology.grounding import (
    MAX_SCHEMA_SUMMARY_CHARS,
    build_source_guide,
    describe_grounding,
    normalize_ontology_schema,
)


def raw_schema() -> dict[str, object]:
    return {
        "values": [
            {
                "id": "36585946254211",
                "namespace": "usertypes",
                "name": "patients",
                "entityIdParts": ["patient-key"],
                "properties": [
                    {"id": "patient-key", "name": "PatientId", "valueType": "BigInt"},
                    {"id": "name", "name": "FirstName", "valueType": "String"},
                    {"id": "dob", "name": "DateOfBirth", "valueType": "DateTime"},
                ],
                "timeseriesProperties": [],
                "mappings": [
                    {
                        "mappingConfiguration": {
                            "sourceTableProperties": {
                                "workspaceId": "44444444-4444-4444-4444-444444444444",
                                "itemId": "66666666-6666-6666-6666-666666666666",
                                "sourceTableName": "patients",
                            }
                        }
                    }
                ],
                "etag": "opaque-etag",
            }
        ]
    }


def test_normalizes_schema_without_topology_or_raw_identifiers() -> None:
    normalized = normalize_ontology_schema(raw_schema())

    assert normalized == {
        "entities": [
            {
                "name": "patients",
                "keyProperties": ["PatientId"],
                "properties": [
                    {"name": "DateOfBirth", "valueType": "DateTime"},
                    {"name": "FirstName", "valueType": "String"},
                    {"name": "PatientId", "valueType": "BigInt"},
                ],
                "timeSeriesProperties": [],
            }
        ]
    }
    serialized = json.dumps(normalized)
    for forbidden in ("36585946254211", "patient-key", "workspaceId", "itemId", "sourceTableName", "etag"):
        assert forbidden not in serialized


def enriched_schema() -> dict[str, object]:
    schema = raw_schema()
    values = schema["values"]
    assert isinstance(values, list)
    entity = values[0]
    assert isinstance(entity, dict)
    entity["semanticEnrichment"] = {
        "description": "Admitted people, one row per key.",
        "synonyms": ["residents", "admissions"],
        "customAttributes": {"owner": "operations"},
    }
    properties = entity["properties"]
    assert isinstance(properties, list)
    date_of_birth = properties[2]
    assert isinstance(date_of_birth, dict)
    date_of_birth["semanticEnrichment"] = {"description": "Birth date; age is not stored and must be derived."}
    return schema


def test_curated_meaning_reaches_the_normalized_grounding() -> None:
    # Stripping this left the deep-analysis agent guessing which values a filter may use, while the
    # interactive path was answering from the very same descriptions.
    normalized = normalize_ontology_schema(enriched_schema())

    assert normalized == {
        "entities": [
            {
                "name": "patients",
                "description": "Admitted people, one row per key.",
                "synonyms": ["admissions", "residents"],
                "keyProperties": ["PatientId"],
                "properties": [
                    {
                        "name": "DateOfBirth",
                        "valueType": "DateTime",
                        "description": "Birth date; age is not stored and must be derived.",
                    },
                    {"name": "FirstName", "valueType": "String"},
                    {"name": "PatientId", "valueType": "BigInt"},
                ],
                "timeSeriesProperties": [],
            }
        ]
    }
    assert "operations" not in json.dumps(normalized)


def test_refuses_malformed_enrichment_instead_of_grounding_on_it() -> None:
    schema = enriched_schema()
    values = schema["values"]
    assert isinstance(values, list)
    entity = values[0]
    assert isinstance(entity, dict)
    entity["semanticEnrichment"] = {"synonyms": ["units", ""]}

    with pytest.raises(ValueError, match="synonym"):
        normalize_ontology_schema(schema)


def test_curated_text_cannot_carry_topology_that_the_rest_of_normalization_strips() -> None:
    # Every other field is stripped of identifiers and endpoints; free text must not reopen that door.
    schema = enriched_schema()
    values = schema["values"]
    assert isinstance(values, list)
    entity = values[0]
    assert isinstance(entity, dict)
    entity["semanticEnrichment"] = {
        "description": "Mapped from 44444444-4444-4444-4444-444444444444 at https://fabric.example/x",
        "synonyms": ["44444444-4444-4444-4444-444444444444"],
    }

    serialized = json.dumps(normalize_ontology_schema(schema))

    assert "44444444-4444-4444-4444-444444444444" not in serialized
    assert "https://fabric.example/x" not in serialized
    assert "[id]" in serialized
    assert "[link]" in serialized


def test_grounding_renders_so_a_schema_call_can_answer_without_a_second_read() -> None:
    described = describe_grounding(normalize_ontology_schema(enriched_schema()))

    assert described == (
        "patients (also: admissions, residents): "
        "DateOfBirth DateTime (Birth date; age is not stored and must be derived.); "
        "FirstName String; PatientId BigInt "
        "-- Admitted people, one row per key."
    )


def test_an_oversized_grounding_is_cut_between_entities_never_inside_one() -> None:
    normalized = {
        "entities": [
            {
                "name": f"entity{index:03d}",
                "description": "d" * 500,
                "keyProperties": [],
                "properties": [{"name": "Prop", "valueType": "String"}],
                "timeSeriesProperties": [],
            }
            for index in range(40)
        ]
    }

    described = describe_grounding(normalized)
    rendered = described.split(" | ")

    assert len(described) <= MAX_SCHEMA_SUMMARY_CHARS
    assert rendered[-1].endswith("omitted here; read the artifact for the rest")
    # Cutting inside an entity would leave a half-listed set of stored values behind.
    assert all("d" * 500 in chunk for chunk in rendered[:-1])


def test_rejects_dangling_entity_key_reference() -> None:
    schema = raw_schema()
    values = schema["values"]
    assert isinstance(values, list)
    entity = values[0]
    assert isinstance(entity, dict)
    entity["entityIdParts"] = ["missing-property"]

    with pytest.raises(ValueError, match="key"):
        normalize_ontology_schema(schema)


def test_builds_sorted_automatic_source_guide_from_safe_schema() -> None:
    guide = build_source_guide(
        alias="lamna-healthcare",
        description="Synthetic hospital operations.",
        normalized_schema=normalize_ontology_schema(raw_schema()),
        routing_terms=(),
    )

    assert guide["routingMode"] == "automatic"
    assert guide["routingTerms"] == ["PatientId", "patients"]
    assert "workspace" not in json.dumps(guide).lower()


def test_requires_curated_terms_when_discovery_terms_exceed_limit() -> None:
    normalized_schema = {
        "entities": [
            {
                "name": f"entity-{index:03d}",
                "keyProperties": [f"key-{index:03d}"],
                "properties": [{"name": f"property-{index:03d}", "valueType": "String"}],
                "timeSeriesProperties": [{"name": f"telemetry-{index:03d}", "valueType": "Double"}],
            }
            for index in range(129)
        ]
    }

    with pytest.raises(ValueError, match="routing terms"):
        build_source_guide(
            alias="lamna-healthcare",
            description="Synthetic hospital operations.",
            normalized_schema=normalized_schema,
            routing_terms=(),
        )

    curated = build_source_guide(
        alias="lamna-healthcare",
        description="Synthetic hospital operations.",
        normalized_schema=normalized_schema,
        routing_terms=("patient census", "room assignment"),
    )

    assert curated["routingMode"] == "curated"
    assert curated["routingTerms"] == ["patient census", "room assignment"]
