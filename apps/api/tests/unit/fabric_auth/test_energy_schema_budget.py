from __future__ import annotations

from eda_api.fabric_auth.ontology import MAX_ONTOLOGY_SCHEMA_CHARS, ontology_schema


def test_operational_schema_preserves_units_grain_and_types_beyond_lamna_budget() -> None:
    values = [
        {
            "name": f"Observation{index}",
            "semanticEnrichment": {
                "description": "One observed well-day, not a stabilized well test; missing is not zero.",
                "synonyms": [f"pengamatan{index}"],
            },
            "properties": [
                {"name": "ObservationId", "valueType": "String"},
                {
                    "name": "OilStb",
                    "valueType": "Double",
                    "semanticEnrichment": {"description": "Stock-tank barrels during the reporting day; not bopd."},
                },
                {
                    "name": "DayStartUtc",
                    "valueType": "DateTime",
                    "semanticEnrichment": {"description": "UTC instant corresponding to local WIB midnight."},
                },
            ],
        }
        for index in range(13)
    ]
    schema = ontology_schema({"structuredContent": {"values": values}})
    assert 2_000 < len(schema) < MAX_ONTOLOGY_SCHEMA_CHARS
    assert schema.count("missing is not zero") == 13
    assert schema.count("OilStb:Double") == 13
    assert schema.count("DayStartUtc:DateTime") == 13
    assert "pengamatan12" in schema


def test_compact_fallback_retains_numeric_vs_timestamp_types() -> None:
    schema = ontology_schema(
        {
            "structuredContent": {
                "values": [
                    {
                        "name": "Observation",
                        "semanticEnrichment": {"description": "x" * MAX_ONTOLOGY_SCHEMA_CHARS},
                        "properties": [
                            {"name": "OilStb", "valueType": "Double"},
                            {"name": "DayStartUtc", "valueType": "DateTime"},
                        ],
                    }
                ]
            }
        }
    )
    assert schema == "Observation: OilStb:Double; DayStartUtc:DateTime"
