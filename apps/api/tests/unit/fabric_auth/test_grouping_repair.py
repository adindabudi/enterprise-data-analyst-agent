"""Grouping repair: the shape the model gets wrong most often, fixed at the source without a model turn.

Fabric's GQL groups only by variables. The engine refused, on 2026-09-30 and 2026-10-01:
  RETURN h.HospitalName AS n, count(*) AS c GROUP BY n            (projected property, grouped by its alias)
  RETURN h.HospitalName AS n, count(*) AS c GROUP BY h.HospitalName  (property expression in GROUP BY)
and ran the same query with the value bound by LET first.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import pytest
from eda_api.fabric_auth.graph import FabricGraphQueryService, GraphStatementError, repair_grouping
from eda_api.fabric_ontology import OntologyTarget
from eda_runtime_state.models import TaskPartition

PARTITION = TaskPartition(
    tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
    owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
    session_id="ses_interactive_12345678",
)
TARGET = OntologyTarget.model_validate(
    {
        "workspaceId": "b82afbde-8304-44c0-ac94-3cf69f6da909",
        "ontologyId": "5566b159-6998-4bb8-a167-6d5bf3c43352",
        "graphModelId": "bdf1d01e-7da8-4b58-873a-54d8edb12f34",
        "description": "Lamna healthcare operations ontology",
    }
)


@pytest.mark.parametrize(
    ("written", "repaired"),
    [
        (
            "MATCH (h:hospitals) RETURN h.HospitalName AS n, count(*) AS c GROUP BY n",
            "MATCH (h:hospitals) LET n = h.HospitalName RETURN n, count(*) AS c GROUP BY n",
        ),
        (
            "MATCH (h:hospitals) RETURN h.HospitalName AS n, count(*) AS c GROUP BY h.HospitalName",
            "MATCH (h:hospitals) LET n = h.HospitalName RETURN n, count(*) AS c GROUP BY n",
        ),
        (
            "MATCH (h:hospitals) RETURN h.HospitalName, count(*) AS c GROUP BY h.HospitalName ORDER BY c DESC",
            "MATCH (h:hospitals) LET HospitalName = h.HospitalName RETURN HospitalName, count(*) AS c "
            "GROUP BY HospitalName ORDER BY c DESC",
        ),
        # The Q4 failure: two keys and a top-1, with ORDER BY and LIMIT kept as written.
        (
            "MATCH (p:patients)-[:patients_has_rooms]->(r:rooms)-[:rooms_has_departments]->(d:departments)"
            "-[:departments_has_hospitals]->(h:hospitals) RETURN h.HospitalName AS hn, d.DepartmentName AS dn, "
            "count(*) AS n GROUP BY hn, dn ORDER BY n DESC LIMIT 1",
            "MATCH (p:patients)-[:patients_has_rooms]->(r:rooms)-[:rooms_has_departments]->(d:departments)"
            "-[:departments_has_hospitals]->(h:hospitals) LET hn = h.HospitalName, dn = d.DepartmentName "
            "RETURN hn, dn, count(*) AS n GROUP BY hn, dn ORDER BY n DESC LIMIT 1",
        ),
        # A key that is already a LET variable stays; only the projected property moves into LET.
        (
            "MATCH (d:departments)-[:departments_has_hospitals]->(h:hospitals) LET hn = h.HospitalName "
            "RETURN hn, d.DepartmentName AS dn, count(*) AS c GROUP BY hn, dn",
            "MATCH (d:departments)-[:departments_has_hospitals]->(h:hospitals) LET hn = h.HospitalName "
            "LET dn = d.DepartmentName RETURN hn, dn, count(*) AS c GROUP BY hn, dn",
        ),
        # A ratio is an aggregate even when it does not start with one.
        (
            "MATCH (r:rooms) OPTIONAL MATCH (p:patients)-[:patients_has_rooms]->(r) "
            "RETURN r.RoomType AS t, 100.0 * count(p) / count(*) AS pct GROUP BY t",
            "MATCH (r:rooms) OPTIONAL MATCH (p:patients)-[:patients_has_rooms]->(r) "
            "LET t = r.RoomType RETURN t, 100.0 * count(p) / count(*) AS pct GROUP BY t",
        ),
        # Only the grouped stage changes; NEXT and what follows are kept.
        (
            "MATCH (r:rooms)-[:rooms_has_departments]->(d:departments) RETURN d.DepartmentName AS dn, "
            "count(*) AS c GROUP BY dn NEXT FILTER c > 80 RETURN dn, c ORDER BY c DESC",
            "MATCH (r:rooms)-[:rooms_has_departments]->(d:departments) LET dn = d.DepartmentName RETURN dn, "
            "count(*) AS c GROUP BY dn NEXT FILTER c > 80 RETURN dn, c ORDER BY c DESC",
        ),
        (
            "MATCH (h:hospitals) RETURN DISTINCT h.State AS s, count(*) AS c GROUP BY s",
            "MATCH (h:hospitals) LET s = h.State RETURN DISTINCT s, count(*) AS c GROUP BY s",
        ),
    ],
)
def test_a_grouped_projection_is_bound_with_let_first(written: str, repaired: str) -> None:
    assert repair_grouping(written) == repaired


@pytest.mark.parametrize(
    "written",
    [
        # Already correct: nothing to repair.
        "MATCH (h:hospitals) LET n = h.HospitalName RETURN n, count(*) AS c GROUP BY n",
        # No grouping at all.
        "MATCH (h:hospitals) RETURN h.HospitalName AS n",
        # A projected value missing from GROUP BY: adding it would change the groups.
        "MATCH (d:departments)-[:departments_has_hospitals]->(h:hospitals) LET hn = h.HospitalName "
        "RETURN hn, d.DepartmentName AS dn, count(*) AS c GROUP BY hn",
        # An unnamed expression has no name to bind.
        "MATCH (h:hospitals) RETURN upper(h.HospitalName), count(*) AS c GROUP BY upper(h.HospitalName)",
        # A generated name that collides with an existing key is left alone.
        "MATCH (h:hospitals) LET HospitalName = h.City RETURN HospitalName, h.HospitalName, count(*) AS c "
        "GROUP BY HospitalName, h.HospitalName",
    ],
)
def test_anything_uncertain_is_left_to_the_model(written: str) -> None:
    assert repair_grouping(written) is None


def test_keywords_inside_strings_and_property_names_are_not_read_as_clauses() -> None:
    written = "MATCH (r:rooms WHERE r.RoomType = 'RETURN, GROUP BY x') RETURN r.Return AS t, count(*) AS c GROUP BY t"

    assert repair_grouping(written) == (
        "MATCH (r:rooms WHERE r.RoomType = 'RETURN, GROUP BY x') LET t = r.Return RETURN t, count(*) AS c GROUP BY t"
    )


class Response:
    def __init__(self, payload: object) -> None:
        self._payload = payload
        self.status_code = 200
        self.text = json.dumps(payload)

    def json(self) -> object:
        return self._payload


def refused(reason: str) -> Response:
    return Response({"status": {"code": "42000", "cause": {"code": "42000", "description": reason}}})


def rows(data: list[dict[str, object]]) -> Response:
    return Response({"status": {"code": "00000"}, "result": {"data": data}})


GROUPING_REFUSAL = (
    "error: data exception; The identifier `h.HospitalName` cannot be used, as it is neither part of the "
    "GROUP BY nor an aggregation."
)


class Client:
    def __init__(self, answers: list[Response]) -> None:
        self.answers = answers
        self.sent: list[str] = []

    async def __aenter__(self) -> Client:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def post(self, url: str, **kwargs: Any) -> Response:
        del url
        self.sent.append(json.loads(kwargs["content"])["query"])
        return self.answers.pop(0)


class Token:
    async def acquire(self, partition: TaskPartition) -> str:
        del partition
        return "owner-token"


def service(client: Client) -> FabricGraphQueryService:
    return FabricGraphQueryService(TARGET, token_provider=Token(), client_factory=lambda **_: client)


WRITTEN = "MATCH (h:hospitals) RETURN h.HospitalName AS n, count(*) AS c GROUP BY n"
REPAIRED = "MATCH (h:hospitals) LET n = h.HospitalName RETURN n, count(*) AS c GROUP BY n"


@pytest.mark.asyncio
async def test_a_refused_grouping_is_run_once_more_repaired_and_says_so() -> None:
    client = Client([refused(GROUPING_REFUSAL), rows([{"n": "Cascade General", "c": 5}])])

    evidence = await service(client).execute_evidence(PARTITION, WRITTEN)

    assert client.sent == [WRITTEN, REPAIRED]
    assert evidence.executed_query == REPAIRED
    assert json.loads(evidence.complete) == [{"n": "Cascade General", "c": 5}]


@pytest.mark.asyncio
async def test_other_refusals_are_returned_to_the_model_without_a_retry() -> None:
    client = Client([refused("error: data exception; Unknown property 'HospitalNme'.")])

    with pytest.raises(GraphStatementError, match="Unknown property"):
        await service(client).execute_evidence(PARTITION, WRITTEN)

    assert client.sent == [WRITTEN]


@pytest.mark.asyncio
async def test_when_the_repair_also_fails_the_model_gets_the_reason_for_its_own_query() -> None:
    client = Client([refused(GROUPING_REFUSAL), refused("error: data exception; something else entirely")])

    with pytest.raises(GraphStatementError, match="neither part of the GROUP BY"):
        await service(client).execute_evidence(PARTITION, WRITTEN)

    assert client.sent == [WRITTEN, REPAIRED]


@pytest.mark.asyncio
async def test_a_query_that_needs_no_repair_runs_once() -> None:
    client = Client([rows([{"n": "x", "c": 1}])])

    evidence = await service(client).execute_evidence(PARTITION, REPAIRED)

    assert client.sent == [REPAIRED] and evidence.executed_query is None
