from __future__ import annotations

import contextlib
import json
from typing import Any
from uuid import UUID

import pytest
from eda_api.fabric_auth.capacity import CapacityMonitor, CapacityState
from eda_api.fabric_auth.graph import FabricGraphQueryService, GraphQueryError, run_graph_query
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
async def test_the_service_calls_the_documented_beta_endpoint() -> None:
    client = Client({"/executeQuery": rows([[1, 24]]), "/items": graph_items()})
    configured = TARGET.model_copy(update={"graph_model_id": UUID(GRAPH_MODEL)})
    service = FabricGraphQueryService(configured, token_provider=Token(), client_factory=factory(client))

    await service.execute(PARTITION, "MATCH (r:rooms) RETURN count(*) AS total")

    [(_, url, _)] = client.requests
    assert url.endswith(f"/v1/workspaces/{WORKSPACE}/graphModels/{GRAPH_MODEL}/executeQuery?beta=true")


@pytest.mark.asyncio
async def test_no_rows_is_an_answer_not_an_error() -> None:
    # The engine says 02000 with an empty table when nothing matched; that is what a filter found.
    empty = Response({"status": {"code": "02000", "description": "note: no data"}, "result": {"data": []}})
    client = Client({"/executeQuery": empty, "/items": graph_items()})
    service = FabricGraphQueryService(TARGET, token_provider=Token(), client_factory=factory(client))

    evidence = await service.execute_evidence(PARTITION, "MATCH (h:hospitals WHERE h.HospitalName = 'x') RETURN h")

    assert evidence.preview == "[]" and evidence.row_count == 0
    assert evidence.source_incomplete is False


class Sequenced(Client):
    """Answers each POST with the next response, as a query that is still running would."""

    def __init__(self, answers: list[Response]) -> None:
        super().__init__({"/items": graph_items()})
        self.answers = answers

    async def post(self, url: str, **kwargs: Any) -> Response:
        self.requests.append(("POST", url, kwargs.get("content") or b""))
        return self.answers.pop(0)


def still_running(token: str) -> Response:
    return Response(
        {
            "status": {"code": "02000", "description": "No data available, retry with continuation token"},
            "result": {"kind": "TABLE", "columns": [], "data": [], "nextPage": token},
        }
    )


@pytest.mark.asyncio
async def test_a_query_still_running_is_followed_to_its_rows_not_read_as_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_wait(seconds: float) -> None:
        del seconds

    monkeypatch.setattr("eda_api.fabric_auth.graph.asyncio.sleep", no_wait)
    client = Sequenced([still_running("a/b+c=="), rows([[1, 24]])])
    service = FabricGraphQueryService(TARGET, token_provider=Token(), client_factory=factory(client))
    query = "MATCH (r:rooms) RETURN count(*) AS total"

    result = await service.execute(PARTITION, query)

    posts = [(url, body) for method, url, body in client.requests if method == "POST"]
    assert len(posts) == 2
    # The token is opaque and percent-encoded once; the body repeats the same query.
    assert posts[1][0].endswith("executeQuery?beta=true&continuationToken=a%2Fb%2Bc%3D%3D")
    assert json.loads(posts[1][1]) == {"query": query}
    assert "24" in result


@pytest.mark.asyncio
async def test_a_query_that_outlives_the_deadline_is_an_unknown_outcome() -> None:
    client = Sequenced([still_running("token")] * 5)
    owner_token = Token().value

    with pytest.raises(GraphQueryError, match="still running"):
        await run_graph_query(
            client,
            url="https://fabric.example/executeQuery?beta=true",
            bearer_token=owner_token,
            statement="MATCH (r:rooms) RETURN r",
            deadline_seconds=0.0,
        )


@pytest.mark.asyncio
async def test_a_truncated_response_is_marked_incomplete() -> None:
    truncated = Response(
        {
            "status": {
                "code": "00000",
                "additionalStatuses": [{"code": "01000", "description": "warning: result truncated"}],
            },
            "result": {"data": [{"n": 1}]},
        }
    )
    client = Client({"/executeQuery": truncated, "/items": graph_items()})
    service = FabricGraphQueryService(TARGET, token_provider=Token(), client_factory=factory(client))

    evidence = await service.execute_evidence(PARTITION, "MATCH (r:rooms) RETURN r.RoomId AS id")

    assert evidence.source_incomplete is True and evidence.row_count == 1


@pytest.mark.asyncio
async def test_a_parse_error_keeps_the_engines_message_and_drops_the_echoed_query() -> None:
    parse_error = Response(
        {
            "status": {
                "code": "42000",
                "description": "error: syntax error or access rule violation",
                "cause": {
                    "code": "42000",
                    "description": (
                        "error: data exception; Syntax error at line 1:58\nOffending token: '.'\n"
                        "MATCH (h:hospitals) RETURN h.HospitalName AS n GROUP BY h.HospitalName\n"
                        "                                                         ^\n"
                        "Error message: mismatched input '.' expecting {<EOF>, WHITESPACE}."
                    ),
                },
            }
        }
    )
    client = Client({"/executeQuery": parse_error, "/items": graph_items()})
    service = FabricGraphQueryService(TARGET, token_provider=Token(), client_factory=factory(client))

    with pytest.raises(GraphQueryError) as failed:
        await service.execute(PARTITION, "MATCH (h:hospitals) RETURN h.HospitalName AS n GROUP BY h.HospitalName")

    reason = str(failed.value)
    assert "mismatched input '.'" in reason and "Syntax error at line 1:58" in reason
    assert "GROUP BY h.HospitalName" not in reason and "^" not in reason


@pytest.mark.asyncio
async def test_a_throttled_query_waits_as_asked_and_runs_once_more(monkeypatch: pytest.MonkeyPatch) -> None:
    waits: list[float] = []

    async def record(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr("eda_api.fabric_auth.graph.asyncio.sleep", record)
    throttled = Response({"errorCode": "TooManyRequests"}, status_code=429)
    throttled.headers = {"Retry-After": "3"}  # type: ignore[attr-defined]
    client = Sequenced([throttled, rows([[1, 24]])])
    service = FabricGraphQueryService(TARGET, token_provider=Token(), client_factory=factory(client))

    await service.execute(PARTITION, "MATCH (r:rooms) RETURN count(*) AS total")

    assert waits == [3.0]
    assert len([method for method, _, _ in client.requests if method == "POST"]) == 2


@pytest.mark.asyncio
async def test_the_service_refuses_a_query_too_long_to_have_been_written_deliberately() -> None:
    client = Client({"/executeQuery": rows([[1, 24]]), "/items": graph_items()})
    service = FabricGraphQueryService(TARGET, token_provider=Token(), client_factory=factory(client))
    runaway = "MATCH (r:rooms) RETURN r" + " OR r.RoomId = 1" * 1_000

    with pytest.raises(GraphQueryError):
        await service.execute(PARTITION, runaway)

    assert client.requests == []


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


@pytest.mark.asyncio
async def test_the_graph_identity_resolved_for_one_principal_is_not_reused_for_another() -> None:
    client = Client({"/executeQuery": rows([[1, 24]]), "/items": graph_items()})
    service = FabricGraphQueryService(TARGET, token_provider=Token(), client_factory=factory(client))
    other = TaskPartition(
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        owner_object_id=UUID("44444444-4444-4444-4444-444444444444"),
        session_id="ses_interactive_87654321",
    )

    await service.execute(PARTITION, "MATCH (r:rooms) RETURN count(*) AS total")
    await service.execute(other, "MATCH (r:rooms) RETURN count(*) AS total")

    # Discovery listed the workspace with each principal's own delegated token; a
    # remembered answer must not stand in for the second caller's permission check.
    assert len([url for method, url, _ in client.requests if method == "GET"]) == 2


def paused_capacity() -> Response:
    return Response(
        {
            "errorCode": "CapacityNotActive",
            "message": "Internal error CapacityNotActive.Capacity ca766f47-3e58-43df-9e2a-28f3cd87a6f8 is not active",
        },
        status_code=404,
    )


@pytest.mark.asyncio
async def test_a_query_refused_for_a_paused_capacity_records_the_pause() -> None:
    monitor = CapacityMonitor()
    client = Client({"/executeQuery": paused_capacity(), "/items": graph_items()})
    service = FabricGraphQueryService(TARGET, token_provider=Token(), client_factory=factory(client), capacity=monitor)

    with pytest.raises(GraphQueryError):
        await service.execute(PARTITION, "MATCH (r:rooms) RETURN count(*) AS total")

    assert monitor.fresh() is CapacityState.PAUSED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answer",
    [rows([[1, 24]]), Response({"status": {"code": "42001", "message": "syntax error"}})],
    ids=["rows", "rejected-query"],
)
async def test_any_answer_from_the_graph_engine_is_a_running_capacity(answer: Response) -> None:
    monitor = CapacityMonitor()
    monitor.record(CapacityState.PAUSED)
    client = Client({"/executeQuery": answer, "/items": graph_items()})
    service = FabricGraphQueryService(TARGET, token_provider=Token(), client_factory=factory(client), capacity=monitor)

    with contextlib.suppress(GraphQueryError):
        await service.execute(PARTITION, "MATCH (r:rooms) RETURN count(*) AS total")

    assert monitor.fresh() is CapacityState.ACTIVE


@pytest.mark.asyncio
async def test_a_refused_token_says_nothing_about_the_capacity() -> None:
    monitor = CapacityMonitor()
    refused = Response({"errorCode": "InsufficientPrivileges", "message": "not authorized"}, status_code=403)
    client = Client({"/executeQuery": refused, "/items": graph_items()})
    service = FabricGraphQueryService(TARGET, token_provider=Token(), client_factory=factory(client), capacity=monitor)

    with pytest.raises(GraphQueryError):
        await service.execute(PARTITION, "MATCH (r:rooms) RETURN count(*) AS total")

    assert monitor.fresh() is None
