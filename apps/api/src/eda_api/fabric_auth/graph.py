"""Direct GQL access to the ontology's graph.

`search_ontology` translates a question with a language model, which costs about
fourteen seconds and silently drops thresholds, second aggregates and whole rows.
The graph behind the same ontology accepts GQL over REST: measured at three to
five seconds, and it returned every group of a grouped ratio exactly, where the
translated path had dropped one group and rounded another to a whole.

Nothing here names an entity type, a property or a value. Whatever the graph
holds is discovered from the graph itself, so a different ontology needs no code
change.

Reads only. The endpoint rejected INSERT and DETACH DELETE outright, and the
statement allowlist here keeps a generated query from reaching for anything else.
"""

from __future__ import annotations

import json
from typing import Any, Protocol, cast

import httpx
from eda_runtime_state.models import TaskPartition

from eda_api.fabric_ontology import OntologyTarget

FABRIC_API = "https://api.fabric.microsoft.com"
GRAPH_ITEM_TYPE = "GraphModel"
MAX_GRAPH_QUERY_CHARS = 8_000
MAX_GRAPH_RESULT_CHARS = 8_000
# Bounded because the reason travels on into a model prompt and the caller's screen.
MAX_FAILURE_DETAIL_CHARS = 200
READ_STATEMENTS = frozenset({"MATCH", "LET"})
# The item definition would name these too, but reading it demands write permission the chat must not hold.
RELATIONSHIP_QUERY = (
    "MATCH (a)-[e]->(b) RETURN labels(e) AS rel, labels(a) AS src, labels(b) AS dst, "
    "count(*) AS n GROUP BY rel, src, dst"
)


class FabricTokenProvider(Protocol):
    async def acquire(self, partition: TaskPartition) -> str: ...


class GraphQueryError(ValueError):
    pass


class FabricGraphQueryService:
    def __init__(
        self,
        target: OntologyTarget,
        *,
        token_provider: FabricTokenProvider,
        client_factory: Any | None = None,
    ) -> None:
        self._target = target
        self._token_provider = token_provider
        self._client_factory = client_factory or httpx.AsyncClient
        self._graph_model_id: str | None = None
        self._relationships: str | None = None

    async def relationships(self, partition: TaskPartition) -> str:
        """Edge names with their direction; a traversal cannot be written without them."""
        if self._relationships is not None:
            return self._relationships
        rows = await self.execute(partition, RELATIONSHIP_QUERY)
        self._relationships = _relationship_summary(rows)
        return self._relationships

    async def execute(self, partition: TaskPartition, query: str) -> str:
        statement = query.strip()
        if not 3 <= len(statement) <= MAX_GRAPH_QUERY_CHARS:
            raise GraphQueryError("graph query must contain 3-8000 characters")
        leading = statement.split(None, 1)[0].upper() if statement.split() else ""
        if leading not in READ_STATEMENTS:
            raise GraphQueryError(f"graph query must start with {' or '.join(sorted(READ_STATEMENTS))}")
        bearer_token = await self._token_provider.acquire(partition)
        if not bearer_token:
            raise GraphQueryError("Fabric owner token is required")
        async with self._client_factory(timeout=httpx.Timeout(60, connect=10), follow_redirects=False) as client:
            graph_model_id = await self._resolve_graph_model(client, bearer_token)
            response = await client.post(
                f"{FABRIC_API}/v1/workspaces/{self._target.workspace_id}"
                f"/GraphModels/{graph_model_id}/executeQuery?preview=true",
                headers=_headers(bearer_token),
                content=json.dumps({"query": statement}).encode(),
            )
        return _graph_result(response)

    async def _resolve_graph_model(self, client: Any, bearer_token: str) -> str:
        if self._graph_model_id is not None:
            return self._graph_model_id
        # Listing a workspace needs Workspace.Read.All, which is a great deal more than reading one
        # graph is worth, so a configured ID is the normal path and discovery only a convenience.
        if self._target.graph_model_id is not None:
            self._graph_model_id = str(self._target.graph_model_id)
            return self._graph_model_id
        response = await client.get(
            f"{FABRIC_API}/v1/workspaces/{self._target.workspace_id}/items?type={GRAPH_ITEM_TYPE}",
            headers=_headers(bearer_token),
        )
        if response.status_code >= 400:
            raise GraphQueryError(
                f"listing the workspace's items returned {response.status_code}; "
                "set graphModelId on this source to read the graph without listing"
            )
        # The generated graph carries the ontology ID in its name, so no extra configuration.
        marker = str(self._target.ontology_id).replace("-", "").lower()
        listed = 0
        for item in _values(response.json()):
            if item.get("type") not in (None, GRAPH_ITEM_TYPE):
                continue
            listed += 1
            name = item.get("displayName")
            item_id = item.get("id")
            if isinstance(name, str) and isinstance(item_id, str) and marker in name.replace("-", "").lower():
                self._graph_model_id = item_id
                return item_id
        # A caller that may see no graphs at all is a different problem from a naming mismatch.
        raise GraphQueryError(f"none of the {listed} graphs visible in this workspace match the ontology")


def _relationship_summary(rows: str) -> str:
    try:
        payload = json.loads(rows)
    except json.JSONDecodeError:
        return ""
    if not isinstance(payload, list):
        return ""
    seen: list[str] = []
    for item in cast(list[object], payload):
        if not isinstance(item, dict):
            continue
        row = cast(dict[str, object], item)
        names = [_label(row.get(key)) for key in ("rel", "src", "dst")]
        if not all(names):
            continue
        edge = f"{names[0]} ({names[1]} -> {names[2]})"
        if edge not in seen:
            seen.append(edge)
    return "; ".join(seen)


def _label(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list) and value:
        first = cast(list[object], value)[0]
        return first if isinstance(first, str) else ""
    return ""


def _headers(bearer_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {bearer_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _values(payload: object) -> list[dict[str, object]]:
    if not isinstance(payload, dict):
        return []
    raw = cast(dict[str, object], payload).get("value")
    if not isinstance(raw, list):
        return []
    return [cast(dict[str, object], item) for item in cast(list[object], raw) if isinstance(item, dict)]


def _failure_detail(response: httpx.Response) -> str:
    """A bare status cannot separate a paused capacity from a deleted graph, and only the body knows."""
    try:
        payload = response.json()
    except ValueError:
        return ""
    if not isinstance(payload, dict):
        return ""
    body = cast(dict[str, object], payload)
    status = body.get("status")
    nested = cast(dict[str, object], status).get("message") if isinstance(status, dict) else None
    named = [part for part in (body.get("errorCode"), body.get("message") or nested) if isinstance(part, str) and part]
    return f": {' - '.join(named)}"[:MAX_FAILURE_DETAIL_CHARS] if named else ""


def _graph_result(response: httpx.Response) -> str:
    if response.status_code >= 400:
        raise GraphQueryError(f"the graph query endpoint returned {response.status_code}{_failure_detail(response)}")
    payload = cast(dict[str, object], response.json())
    status = payload.get("status")
    status_body = cast(dict[str, object], status) if isinstance(status, dict) else {}
    code = status_body.get("code")
    if code != "00000":
        cause = status_body.get("cause") or payload.get("cause")
        detail = cast(dict[str, object], cause).get("description") if isinstance(cause, dict) else None
        reason = detail if isinstance(detail, str) and detail else status_body.get("message")
        raise GraphQueryError(
            str(reason)[:MAX_FAILURE_DETAIL_CHARS]
            if isinstance(reason, str) and reason
            else "the graph rejected the query"
        )
    result = payload.get("result")
    rows = cast(dict[str, object], result).get("data") if isinstance(result, dict) else None
    serialized = json.dumps(rows if rows is not None else [], ensure_ascii=False, separators=(",", ":"))
    if len(serialized) <= MAX_GRAPH_RESULT_CHARS:
        return serialized
    if not isinstance(rows, list):
        raise GraphQueryError("the graph returned more than 8000 characters")
    kept = cast(list[object], rows)
    while kept and len(json.dumps(kept, ensure_ascii=False, separators=(",", ":"))) > MAX_GRAPH_RESULT_CHARS:
        kept = kept[: len(kept) // 2]
    return json.dumps(
        {"rows": kept, "truncated": True, "returnedRows": len(kept), "totalRows": len(cast(list[object], rows))},
        ensure_ascii=False,
        separators=(",", ":"),
    )
