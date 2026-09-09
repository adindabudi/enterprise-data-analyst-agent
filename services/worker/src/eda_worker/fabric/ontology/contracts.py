from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class OntologyQueryPurpose(StrEnum):
    SCHEMA = "schema"
    RELATIONSHIP = "relationship"
    AGGREGATE = "aggregate"
    TIME_SERIES = "time_series"
    CONTROL_TOTAL = "control_total"


class FabricOntologyQueryOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ontology: str = Field(pattern=r"^[a-z][a-z0-9-]{1,39}$")
    purpose: OntologyQueryPurpose
    question: str = Field(min_length=3, max_length=4_000)
