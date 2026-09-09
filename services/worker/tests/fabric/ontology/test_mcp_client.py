from __future__ import annotations

from contextlib import asynccontextmanager
from uuid import UUID

import pytest
from eda_worker.fabric.ontology.config import OntologyTarget
from eda_worker.fabric.ontology.mcp_client import (
    EXPECTED_ONTOLOGY_TOOLS,
    EXPECTED_TOOL_DESCRIPTIONS,
    OntologyMcpClient,
    build_ontology_mcp_url,
    validate_ontology_tools,
)


def target() -> OntologyTarget:
    return OntologyTarget(
        workspace_id=UUID("44444444-4444-4444-4444-444444444444"),
        ontology_id=UUID("55555555-5555-5555-5555-555555555555"),
        description="Synthetic hospital operations.",
    )


def tool(name: str) -> dict[str, object]:
    return {
        "name": name,
        "description": EXPECTED_TOOL_DESCRIPTIONS[name],
        "inputSchema": EXPECTED_ONTOLOGY_TOOLS[name],
    }


def test_the_shared_fabric_host_is_used_without_a_private_link_zone() -> None:
    assert build_ontology_mcp_url(target()) == (
        "https://api.fabric.microsoft.com/v1/mcp/dataPlane/workspaces/"
        "44444444-4444-4444-4444-444444444444/items/55555555-5555-5555-5555-555555555555/ontologyEndpoint"
    )


def test_a_private_link_zone_routes_to_the_workspace_host() -> None:
    private = target().model_copy(update={"private_link_zone": "z12"})

    assert build_ontology_mcp_url(private) == (
        "https://44444444444444444444444444444444.z12.w.api.fabric.microsoft.com/v1/mcp/dataPlane/workspaces/"
        "44444444-4444-4444-4444-444444444444/items/55555555-5555-5555-5555-555555555555/ontologyEndpoint"
    )


@pytest.mark.parametrize("zone", ["evil.example.com", "z", "12", "z1234", "z12/", ""])
def test_an_operator_cannot_redirect_the_endpoint_through_the_zone(zone: str) -> None:
    with pytest.raises(ValueError, match="private_link_zone"):
        OntologyTarget(
            workspace_id=UUID("44444444-4444-4444-4444-444444444444"),
            ontology_id=UUID("55555555-5555-5555-5555-555555555555"),
            description="Synthetic hospital operations.",
            private_link_zone=zone,
        )


def test_builds_only_the_fixed_fabric_item_endpoint() -> None:
    assert build_ontology_mcp_url(target()) == (
        "https://api.fabric.microsoft.com/v1/mcp/dataPlane/workspaces/"
        "44444444-4444-4444-4444-444444444444/items/55555555-5555-5555-5555-555555555555/ontologyEndpoint"
    )


def test_accepts_the_live_two_tool_contract() -> None:
    validated = validate_ontology_tools([tool("list_ontology_entity_types"), tool("search_ontology")])

    assert set(validated) == {"list_ontology_entity_types", "search_ontology"}


def test_rejects_contract_drift() -> None:
    with pytest.raises(ValueError, match="tool contract"):
        validate_ontology_tools([tool("list_ontology_entity_types")])


class FakeSession:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def initialize(self) -> None:
        self.calls.append("initialize")

    async def list_tools(self) -> list[dict[str, object]]:
        self.calls.append("tools/list")
        return [tool("list_ontology_entity_types"), tool("search_ontology")]

    async def call_tool(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        self.calls.append(name)
        return {"structuredContent": {"name": name, "arguments": arguments}}


@pytest.mark.asyncio
async def test_cold_query_uses_one_verified_session_for_list_and_search() -> None:
    session = FakeSession()
    test_bearer_token = "owner" + "-token"

    @asynccontextmanager
    async def open_session(url: str, bearer_token: str):
        assert url == build_ontology_mcp_url(target())
        assert bearer_token == test_bearer_token
        yield session

    async def token_provider() -> str:
        return test_bearer_token

    client = OntologyMcpClient(target(), token_provider=token_provider, session_opener=open_session)

    schema, result = await client.inspect_and_search(question="How many patients are admitted?")

    assert schema == {
        "name": "list_ontology_entity_types",
        "arguments": {"includeProperties": True},
    }
    assert result == {
        "name": "search_ontology",
        "arguments": {
            "naturalLanguageQuery": "How many patients are admitted?",
            "naturalLanguageResponse": False,
        },
    }
    assert session.calls == ["initialize", "tools/list", "list_ontology_entity_types", "search_ontology"]
