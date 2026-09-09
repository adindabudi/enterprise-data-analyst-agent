from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import pytest
from eda_api.fabric_auth.graph import FabricGraphQueryService, GraphQueryError
from eda_api.fabric_ontology import OntologyTarget
from eda_runtime_state.models import TaskPartition

PARTITION = TaskPartition(
    tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
    owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
    session_id="ses_interactive_12345678",
)
WORKSPACE = "b82afbde-8304-44c0-ac94-3cf69f6da909"
ONTOLOGY = "5566b159-6998-4bb8-a167-6d5bf3c43352"
GRAPH_MODEL = "bdf1d01e-7da8-4b58-873a-54d8edb12f34"

TARGET = OntologyTarget.model_validate(
    {
        "workspaceId": WORKSPACE,
        "ontologyId": ONTOLOGY,
        "description": "Lamna healthcare operations ontology",
        "routingTerms": ["patient"],
    }
)


class Token:
    def __init__(self, value: str = "owner-token") -> None:
        self.value = value

    async def acquire(self, partition: TaskPartition) -> str:
        del partition
        return self.value


class Response:
    def __init__(self, payload: object, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def json(self) -> object:
        return self._payload


class Client:
    def __init__(self, routes: dict[str, Response]) -> None:
        self.routes = routes
        self.requests: list[tuple[str, str, bytes]] = []

    async def __aenter__(self) -> Client:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    def _match(self, url: str) -> Response:
        for fragment, response in self.routes.items():
            if fragment in url:
                return response
        raise AssertionError(f"unexpected url {url}")

    async def get(self, url: str, **kwargs: Any) -> Response:
        del kwargs
        self.requests.append(("GET", url, b""))
        return self._match(url)

    async def post(self, url: str, **kwargs: Any) -> Response:
        self.requests.append(("POST", url, kwargs.get("content") or b""))
        return self._match(url)


def factory(client: Client) -> Any:
    def build(**kwargs: Any) -> Client:
        del kwargs
        return client

    return build


def graph_items() -> Response:
    return Response(
        {
            "value": [
                {"id": "other-item", "type": "Lakehouse", "displayName": "unrelated"},
                {"id": GRAPH_MODEL, "type": "GraphModel", "displayName": f"gm{ONTOLOGY.replace('-', '')}"},
            ]
        }
    )


def rows(data: list[list[object]]) -> Response:
    return Response(
        {
            "status": {"code": "00000"},
            "result": {"kind": "TABLE", "columns": [{"name": "dept"}, {"name": "total"}], "data": data},
        }
    )


def edges() -> Response:
    return Response(
        {
            "status": {"code": "00000"},
            "result": {
                "kind": "TABLE",
                "columns": [{"name": "rel"}, {"name": "src"}, {"name": "dst"}, {"name": "n"}],
                "data": [
                    {"rel": ["rooms_has_departments"], "src": ["rooms"], "dst": ["departments"], "n": 414},
                    {"rel": ["patients_has_rooms"], "src": ["patients"], "dst": ["rooms"], "n": 308},
                ],
            },
        }
    )


@pytest.mark.asyncio
async def test_the_service_sends_the_query_the_model_wrote_without_rewriting_it() -> None:
    client = Client({"/executeQuery": rows([[1, 24]]), "/items": graph_items()})
    service = FabricGraphQueryService(TARGET, token_provider=Token(), client_factory=factory(client))
    query = "MATCH (r:rooms)-[:rooms_has_departments]->(d:departments) RETURN d.DepartmentId AS dept"

    result = await service.execute(PARTITION, query)

    sent = [body for method, url, body in client.requests if "executeQuery" in url]
    assert json.loads(sent[0]) == {"query": query}
    assert "24" in result


@pytest.mark.asyncio
async def test_a_configured_graph_is_read_without_listing_the_workspace() -> None:
    refused = Response({"errorCode": "InsufficientPrivileges"}, status_code=403)
    client = Client({"/executeQuery": rows([[1, 24]]), "/items": refused})
    configured = TARGET.model_copy(update={"graph_model_id": UUID(GRAPH_MODEL)})
    service = FabricGraphQueryService(configured, token_provider=Token(), client_factory=factory(client))

    # Listing a workspace needs Workspace.Read.All; naming the graph keeps the delegated pair small.
    await service.execute(PARTITION, "MATCH (r:rooms) RETURN count(*) AS total")

    assert not [url for _, url, _ in client.requests if "/items" in url]
    assert [url for _, url, _ in client.requests if GRAPH_MODEL in url]


@pytest.mark.asyncio
async def test_the_service_says_how_to_avoid_listing_when_the_workspace_refuses() -> None:
    refused = Response({"errorCode": "InsufficientPrivileges"}, status_code=403)
    client = Client({"/executeQuery": rows([[1, 24]]), "/items": refused})
    service = FabricGraphQueryService(TARGET, token_provider=Token(), client_factory=factory(client))

    with pytest.raises(GraphQueryError) as failed:
        await service.execute(PARTITION, "MATCH (d:departments) RETURN count(*) AS n")

    assert "403" in str(failed.value)
    assert "graphModelId" in str(failed.value)


@pytest.mark.asyncio
async def test_the_service_resolves_the_graph_through_the_endpoint_our_scopes_cover() -> None:
    client = Client({"/executeQuery": rows([[1, 24]]), "/items": graph_items()})
    service = FabricGraphQueryService(TARGET, token_provider=Token(), client_factory=factory(client))

    await service.execute(PARTITION, "MATCH (r:rooms) RETURN count(*) AS total")

    resolved = [url for method, url, _ in client.requests if method == "GET"]
    # The type-specific list answered 403 to the delegated pair we hold; /items is what Item.Read.All covers.
    assert resolved and all("/items?type=GraphModel" in url for url in resolved)
    assert not [url for url in resolved if url.endswith("/GraphModels")]


@pytest.mark.asyncio
async def test_the_service_resolves_the_graph_model_once_and_reuses_it() -> None:
    client = Client({"/executeQuery": rows([[1, 24]]), "/items": graph_items()})
    service = FabricGraphQueryService(TARGET, token_provider=Token(), client_factory=factory(client))

    await service.execute(PARTITION, "MATCH (r:rooms) RETURN count(*) AS total")
    await service.execute(PARTITION, "MATCH (d:departments) RETURN count(*) AS total")

    assert len([url for method, url, _ in client.requests if method == "GET"]) == 1


@pytest.mark.parametrize(
    "query",
    [
        "INSERT (r:rooms {RoomId: 1})",
        "DETACH DELETE (r:rooms)",
        "  detach delete (r:rooms)",
        "CALL something()",
    ],
)
@pytest.mark.asyncio
async def test_the_service_refuses_a_query_that_would_change_the_graph(query: str) -> None:
    client = Client({"/executeQuery": rows([[1, 24]]), "/items": graph_items()})
    service = FabricGraphQueryService(TARGET, token_provider=Token(), client_factory=factory(client))

    with pytest.raises(GraphQueryError):
        await service.execute(PARTITION, query)

    assert client.requests == []


@pytest.mark.asyncio
async def test_the_service_reports_a_rejected_query_instead_of_returning_nothing() -> None:
    failure = Response({"status": {"code": "10001", "message": "syntax error near WHERE"}}, status_code=400)
    client = Client({"/executeQuery": failure, "/items": graph_items()})
    service = FabricGraphQueryService(TARGET, token_provider=Token(), client_factory=factory(client))

    with pytest.raises(GraphQueryError) as failed:
        await service.execute(PARTITION, "MATCH (r:rooms) WHERE r.RoomId = 1 RETURN r")

    # A refused token and a mistyped query need different fixes, so the transport status has to survive.
    assert "400" in str(failed.value)


@pytest.mark.asyncio
async def test_the_service_separates_a_refused_token_from_a_mistyped_query() -> None:
    refused = Response({"errorCode": "InsufficientPrivileges", "message": "not authorized"}, status_code=403)
    client = Client({"/executeQuery": refused, "/items": graph_items()})
    service = FabricGraphQueryService(TARGET, token_provider=Token(), client_factory=factory(client))

    with pytest.raises(GraphQueryError) as failed:
        await service.execute(PARTITION, "MATCH (r:rooms) RETURN count(*) AS total")

    assert "403" in str(failed.value)


@pytest.mark.asyncio
async def test_a_paused_capacity_is_named_rather_than_left_as_a_bare_404() -> None:
    # Fabric answers a paused capacity with a 404, which reads exactly like a deleted graph.
    paused = Response(
        {
            "requestId": "b73dd6f3-221e-4ce0-98d3-417f1610f124",
            "errorCode": "CapacityNotActive",
            "message": "Internal error CapacityNotActive.Capacity ca766f47-3e58-43df-9e2a-28f3cd87a6f8 is not active",
            "isRetriable": False,
        },
        status_code=404,
    )
    client = Client({"/executeQuery": paused, "/items": graph_items()})
    service = FabricGraphQueryService(TARGET, token_provider=Token(), client_factory=factory(client))

    with pytest.raises(GraphQueryError) as failed:
        await service.execute(PARTITION, "MATCH (r:rooms) RETURN count(*) AS total")

    assert "CapacityNotActive" in str(failed.value)
    assert "is not active" in str(failed.value)


@pytest.mark.asyncio
async def test_a_failure_reason_cannot_grow_past_the_budget_it_is_quoted_into() -> None:
    shouting = Response({"errorCode": "Bad", "message": "x" * 5_000}, status_code=500)
    client = Client({"/executeQuery": shouting, "/items": graph_items()})
    service = FabricGraphQueryService(TARGET, token_provider=Token(), client_factory=factory(client))

    with pytest.raises(GraphQueryError) as failed:
        await service.execute(PARTITION, "MATCH (r:rooms) RETURN count(*) AS total")

    assert len(str(failed.value)) < 300


@pytest.mark.asyncio
async def test_a_failure_without_a_readable_body_still_reports_the_status() -> None:
    class Unreadable:
        status_code = 502
        text = "<html>gateway</html>"

        def json(self) -> object:
            raise ValueError("not JSON")

    routes: dict[str, Any] = {"/executeQuery": Unreadable(), "/items": graph_items()}
    client = Client(routes)
    service = FabricGraphQueryService(TARGET, token_provider=Token(), client_factory=factory(client))

    with pytest.raises(GraphQueryError) as failed:
        await service.execute(PARTITION, "MATCH (r:rooms) RETURN count(*) AS total")

    assert "502" in str(failed.value)


@pytest.mark.asyncio
async def test_the_service_refuses_a_body_that_carries_a_failure_code_under_a_200() -> None:
    failure = Response({"status": {"code": "10001", "message": "no such property"}}, status_code=200)
    client = Client({"/executeQuery": failure, "/items": graph_items()})
    service = FabricGraphQueryService(TARGET, token_provider=Token(), client_factory=factory(client))

    with pytest.raises(GraphQueryError):
        await service.execute(PARTITION, "MATCH (r:rooms) RETURN r.NoSuchProperty")


@pytest.mark.asyncio
async def test_the_service_returns_the_nested_reason_for_a_rejected_query() -> None:
    failure = Response(
        {
            "status": {
                "code": "42000",
                "cause": {"description": "The relationship pattern does not match any edge type in the graph."},
            }
        },
        status_code=200,
    )
    client = Client({"/executeQuery": failure, "/items": graph_items()})
    service = FabricGraphQueryService(TARGET, token_provider=Token(), client_factory=factory(client))

    with pytest.raises(GraphQueryError) as failed:
        await service.execute(PARTITION, "MATCH (a)-[]->(b) RETURN a")

    assert "does not match any edge type" in str(failed.value)


@pytest.mark.asyncio
async def test_the_service_names_each_relationship_with_the_direction_a_traversal_needs() -> None:
    client = Client({"/executeQuery": edges(), "/items": graph_items()})
    service = FabricGraphQueryService(TARGET, token_provider=Token(), client_factory=factory(client))

    summary = await service.relationships(PARTITION)

    assert summary == "rooms_has_departments (rooms -> departments); patients_has_rooms (patients -> rooms)"
    posts = len([url for method, url, _ in client.requests if "executeQuery" in url])
    await service.relationships(PARTITION)
    assert len([url for method, url, _ in client.requests if "executeQuery" in url]) == posts


@pytest.mark.asyncio
async def test_the_service_leaves_out_edges_it_could_not_name() -> None:
    ragged = Response(
        {
            "status": {"code": "00000"},
            "result": {
                "kind": "TABLE",
                "columns": [{"name": "rel"}, {"name": "src"}, {"name": "dst"}],
                "data": [
                    {"rel": ["rooms_has_departments"], "src": ["rooms"], "dst": ["departments"]},
                    {"rel": ["rooms_has_departments"], "src": ["rooms"], "dst": ["departments"]},
                    {"rel": [], "src": ["rooms"], "dst": []},
                ],
            },
        }
    )
    client = Client({"/executeQuery": ragged, "/items": graph_items()})
    service = FabricGraphQueryService(TARGET, token_provider=Token(), client_factory=factory(client))

    # A half-named edge reads as a real one, and the model would then write a traversal that cannot run.
    assert await service.relationships(PARTITION) == "rooms_has_departments (rooms -> departments)"


@pytest.mark.asyncio
async def test_the_service_reads_relationships_without_asking_for_write_permission() -> None:
    client = Client({"/executeQuery": edges(), "/items": graph_items()})
    service = FabricGraphQueryService(TARGET, token_provider=Token(), client_factory=factory(client))

    await service.relationships(PARTITION)

    # getDefinition would name these too, but Fabric requires Item.ReadWrite.All for it.
    assert not [url for _, url, _ in client.requests if "getDefinition" in url]


@pytest.mark.asyncio
async def test_the_service_refuses_a_query_too_long_to_have_been_written_deliberately() -> None:
    client = Client({"/executeQuery": rows([[1, 24]]), "/items": graph_items()})
    service = FabricGraphQueryService(TARGET, token_provider=Token(), client_factory=factory(client))
    runaway = "MATCH (r:rooms) RETURN r" + " OR r.RoomId = 1" * 1_000

    with pytest.raises(GraphQueryError):
        await service.execute(PARTITION, runaway)

    assert client.requests == []


@pytest.mark.asyncio
async def test_the_service_refuses_to_pass_off_an_unreadable_definition_as_no_relationships() -> None:
    refused = Response({"errorCode": "InsufficientPrivileges"}, status_code=403)
    client = Client({"/executeQuery": refused, "/items": graph_items()})
    service = FabricGraphQueryService(TARGET, token_provider=Token(), client_factory=factory(client))

    # An empty summary and an unreadable one look identical to the model, so only one of them may be returned.
    with pytest.raises(GraphQueryError):
        await service.relationships(PARTITION)


@pytest.mark.asyncio
async def test_the_service_refuses_to_query_without_an_owner_token() -> None:
    client = Client({"/executeQuery": rows([[1, 24]]), "/items": graph_items()})
    service = FabricGraphQueryService(TARGET, token_provider=Token(""), client_factory=factory(client))

    with pytest.raises(GraphQueryError):
        await service.execute(PARTITION, "MATCH (r:rooms) RETURN count(*) AS total")

    assert client.requests == []


@pytest.mark.asyncio
async def test_the_service_reports_the_status_when_the_workspace_refuses_to_list_graphs() -> None:
    refused = Response({"errorCode": "InsufficientPrivileges"}, status_code=403)
    client = Client({"/executeQuery": rows([[1, 24]]), "/items": refused})
    service = FabricGraphQueryService(TARGET, token_provider=Token(), client_factory=factory(client))

    with pytest.raises(GraphQueryError) as failed:
        await service.execute(PARTITION, "MATCH (d:departments) RETURN count(*) AS n")

    # A scoped token can list fewer items than a broad one, so the status has to survive to the log.
    assert "403" in str(failed.value)


@pytest.mark.parametrize(
    ("visible", "expected"),
    [
        ([], "0 graphs"),
        ([{"id": "other", "type": "GraphModel", "displayName": "unrelated-graph"}], "1 graphs"),
        # A workspace full of other item types must not read as graphs the caller could not match.
        ([{"id": "lh", "type": "Lakehouse", "displayName": "unrelated"}], "0 graphs"),
    ],
)
@pytest.mark.asyncio
async def test_the_service_says_how_many_graphs_it_could_see(visible: list[dict[str, str]], expected: str) -> None:
    client = Client({"/executeQuery": rows([[1, 24]]), "/items": Response({"value": visible})})
    service = FabricGraphQueryService(TARGET, token_provider=Token(), client_factory=factory(client))

    with pytest.raises(GraphQueryError) as failed:
        await service.execute(PARTITION, "MATCH (d:departments) RETURN count(*) AS n")

    # Seeing no graphs is a permission problem; seeing graphs that don't match is a naming problem.
    assert expected in str(failed.value)
