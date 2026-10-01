from __future__ import annotations

from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

ONTOLOGY_ENDPOINT_TEMPLATE = (
    "https://api.fabric.microsoft.com/v1/mcp/dataPlane/workspaces/{workspace_id}/items/{ontology_id}/ontologyEndpoint"
)
# The Microsoft-hosted remote MCP server of one KQL database, which runs the KQL an agent writes.
KQL_ENDPOINT_TEMPLATE = (
    "https://api.fabric.microsoft.com/v1/mcp/dataPlane/workspaces/{workspace_id}/items/{kql_database_id}/kqlEndpoint"
)
FABRIC_SHARED_HOST = "api.fabric.microsoft.com"


class OntologyTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    workspace_id: UUID = Field(alias="workspaceId")
    ontology_id: UUID = Field(alias="ontologyId")
    # Discovering this costs Workspace.Read.All, which is far more than reading one graph is worth.
    graph_model_id: UUID | None = Field(default=None, alias="graphModelId")
    # The KQL database behind the ontology's time-series bindings. Without it, time series stay unreadable.
    kql_database_id: UUID | None = Field(default=None, alias="kqlDatabaseId")
    description: str = Field(min_length=1, max_length=240, pattern=r"^[^\r\n\x00-\x1f<>]+$")
    routing_terms: tuple[str, ...] = Field(default=(), alias="routingTerms", max_length=64)
    private_link_zone: str | None = Field(default=None, alias="privateLinkZone", pattern=r"^z[0-9]{1,3}$")

    @model_validator(mode="after")
    def validate_target(self) -> Self:
        if self.workspace_id == self.ontology_id:
            raise ValueError("workspace and ontology IDs must differ")
        if self.graph_model_id in {self.workspace_id, self.ontology_id}:
            raise ValueError("graph model ID must differ from the workspace and ontology IDs")
        if self.kql_database_id is not None and self.kql_database_id in {
            self.workspace_id,
            self.ontology_id,
            self.graph_model_id,
        }:
            raise ValueError("KQL database ID must differ from the workspace, ontology and graph model IDs")
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
        return self._on_workspace_host(
            ONTOLOGY_ENDPOINT_TEMPLATE.format(workspace_id=self.workspace_id, ontology_id=self.ontology_id)
        )

    @property
    def kql_endpoint(self) -> str | None:
        if self.kql_database_id is None:
            return None
        return self._on_workspace_host(
            KQL_ENDPOINT_TEMPLATE.format(workspace_id=self.workspace_id, kql_database_id=self.kql_database_id)
        )

    def _on_workspace_host(self, endpoint: str) -> str:
        if self.private_link_zone is None:
            return endpoint
        # The host is derived from the target itself so no operator value reaches the request URL.
        host = f"{self.workspace_id.hex}.{self.private_link_zone}.w.{FABRIC_SHARED_HOST}"
        return endpoint.replace(f"//{FABRIC_SHARED_HOST}/", f"//{host}/", 1)
