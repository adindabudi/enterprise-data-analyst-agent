from __future__ import annotations

from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

ONTOLOGY_ENDPOINT_TEMPLATE = (
    "https://api.fabric.microsoft.com/v1/mcp/dataPlane/workspaces/{workspace_id}/items/{ontology_id}/ontologyEndpoint"
)
FABRIC_SHARED_HOST = "api.fabric.microsoft.com"


class OntologyTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    workspace_id: UUID = Field(alias="workspaceId")
    ontology_id: UUID = Field(alias="ontologyId")
    # The API reads the same document and pins the graph here; both readers forbid extras.
    graph_model_id: UUID | None = Field(default=None, alias="graphModelId")
    # Read only by the API, whose time-series tool reaches this KQL database; declared so the shared document parses.
    kql_database_id: UUID | None = Field(default=None, alias="kqlDatabaseId")
    description: str = Field(min_length=1, max_length=240, pattern=r"^[^\r\n\x00-\x1f<>]+$")
    routing_terms: tuple[str, ...] = Field(default=(), alias="routingTerms", max_length=64)
    private_link_zone: str | None = Field(default=None, alias="privateLinkZone", pattern=r"^z[0-9]{1,3}$")

    @model_validator(mode="after")
    def validate_target(self) -> Self:
        if self.workspace_id == self.ontology_id:
            raise ValueError("workspace and ontology IDs must differ")
        if len(set(self.routing_terms)) != len(self.routing_terms):
            raise ValueError("routing terms must be unique")
        for term in self.routing_terms:
            if term != term.strip() or not 1 <= len(term) <= 64:
                raise ValueError("routing terms must be trimmed and 1-64 characters")
            if any(ord(character) < 32 or character in "<>" for character in term):
                raise ValueError("routing terms contain a forbidden character")
        return self

    @property
    def endpoint(self) -> str:
        endpoint = ONTOLOGY_ENDPOINT_TEMPLATE.format(workspace_id=self.workspace_id, ontology_id=self.ontology_id)
        if self.private_link_zone is None:
            return endpoint
        # The host is derived from the target itself so no operator value reaches the request URL.
        host = f"{self.workspace_id.hex}.{self.private_link_zone}.w.{FABRIC_SHARED_HOST}"
        return endpoint.replace(f"//{FABRIC_SHARED_HOST}/", f"//{host}/", 1)
