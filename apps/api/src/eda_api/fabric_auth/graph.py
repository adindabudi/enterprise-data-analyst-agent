"""Direct GQL access to the ontology's graph.

The agent writes the GQL itself, from the schema snapshot pinned in its
instructions (see `snapshot`), and this module runs exactly that statement
through the documented GQL Query API. `search_ontology` translated a question
with a language model instead, which cost about fourteen seconds and silently
dropped thresholds, second aggregates and whole rows; the same questions written
as GQL came back in one to five seconds with every group of a grouped ratio exact.

Nothing here names an entity type, a property or a value, so a different
ontology needs no code change.

Reads only. The endpoint rejected INSERT and DETACH DELETE outright, and the
statement allowlist here keeps a generated query from reaching for anything else.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from itertools import pairwise
from typing import Any, Protocol, cast
from urllib.parse import quote

import httpx
from eda_runtime_state.models import TaskPartition

from eda_api.fabric_ontology import OntologyTarget

from .capacity import CAPACITY_NOT_ACTIVE, CapacityMonitor, CapacityState
from .metadata_cache import MetadataCache, MetadataKey

FABRIC_API = "https://api.fabric.microsoft.com"
GRAPH_ITEM_TYPE = "GraphModel"
MAX_GRAPH_QUERY_CHARS = 8_000
MAX_GRAPH_RESULT_CHARS = 8_000
# Bounded because the reason travels on into a model prompt and the caller's screen.
MAX_FAILURE_DETAIL_CHARS = 200
READ_STATEMENTS = frozenset({"MATCH", "LET"})
# The API keeps a query running for up to 20 minutes across continuation polls. A tool
# call gives up long before that: a query this slow needs narrowing, not more patience.
GRAPH_QUERY_DEADLINE_SECONDS = 120.0
CONTINUATION_POLL_SECONDS = 1.0
MAX_THROTTLE_RETRIES = 2
MAX_RETRY_AFTER_SECONDS = 10.0
# Public status codes of the GQL Query API.
GQL_SUCCESS = "00000"
GQL_WARNING = "01000"
GQL_NO_DATA = "02000"
GQL_USER_ERROR = "42000"
_ROW_STATUSES = frozenset({GQL_SUCCESS, GQL_WARNING, GQL_NO_DATA})


class FabricTokenProvider(Protocol):
    async def acquire(self, partition: TaskPartition) -> str: ...


class GraphQueryError(ValueError):
    pass


class GraphStatementError(GraphQueryError):
    """The engine read the statement and refused it, so the statement itself has to change."""


@dataclass(frozen=True)
class QueryEvidence:
    """What a source returned, kept whole, beside the bounded slice a model can read.

    Only the preview travels into the prompt. The complete rows become the task's
    evidence, so a workbook or chart is computed from everything the source
    returned rather than from the part that fit the context budget.

    `source_incomplete` is a different matter from a shortened preview: the source
    itself did not return every row, so even the complete evidence is partial and
    no total may be reported from it. `executed_query` is set only when the source
    ran a repaired form of the query it was given.
    """

    complete: str
    preview: str
    row_count: int | None
    preview_truncated: bool
    source_incomplete: bool = False
    executed_query: str | None = None


def rows_evidence(rows: list[object], *, max_chars: int, source_incomplete: bool = False) -> QueryEvidence:
    """Keep every row, and hand the model the largest leading slice that fits its budget."""
    serialized = _serialize(rows)
    if len(serialized) <= max_chars:
        return QueryEvidence(
            complete=serialized,
            preview=serialized,
            row_count=len(rows),
            preview_truncated=False,
            source_incomplete=source_incomplete,
        )
    kept = rows
    while kept and len(_serialize(kept)) > max_chars:
        kept = kept[: len(kept) // 2]
    preview = _serialize({"rows": kept, "truncated": True, "returnedRows": len(kept), "totalRows": len(rows)})
    return QueryEvidence(
        complete=serialized,
        preview=preview,
        row_count=len(rows),
        preview_truncated=True,
        source_incomplete=source_incomplete,
    )


def validate_graph_statement(query: str) -> str:
    statement = query.strip()
    if not 3 <= len(statement) <= MAX_GRAPH_QUERY_CHARS:
        raise GraphQueryError(f"graph query must contain 3-{MAX_GRAPH_QUERY_CHARS} characters")
    leading = statement.split(None, 1)[0].upper() if statement.split() else ""
    if leading not in READ_STATEMENTS:
        raise GraphQueryError(f"graph query must start with {' or '.join(sorted(READ_STATEMENTS))}")
    return statement


def graph_query_url(workspace_id: object, graph_model_id: object) -> str:
    return f"{FABRIC_API}/v1/workspaces/{workspace_id}/graphModels/{graph_model_id}/executeQuery?beta=true"


async def run_graph_query(
    client: Any,
    *,
    url: str,
    bearer_token: str,
    statement: str,
    observe: Callable[[httpx.Response], None] | None = None,
    deadline_seconds: float = GRAPH_QUERY_DEADLINE_SECONDS,
    poll_seconds: float = CONTINUATION_POLL_SECONDS,
) -> QueryEvidence:
    """One statement through the GQL Query API, following its continuation until rows arrive.

    `02000` means two things: no rows, or still running with a `nextPage` token. Only
    the token tells them apart, so an unfinished query is never read as an empty one.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + deadline_seconds
    body = json.dumps({"query": statement}).encode()
    target = url
    throttled = 0
    while True:
        response = await client.post(target, headers=_headers(bearer_token), content=body)
        if observe is not None:
            observe(response)
        if response.status_code == 429 and throttled < MAX_THROTTLE_RETRIES:
            throttled += 1
            await asyncio.sleep(_retry_after(response))
            continue
        if response.status_code >= 400:
            raise GraphQueryError(
                f"the graph query endpoint returned {response.status_code}{_failure_detail(response)}"
            )
        payload = _payload(response)
        result = _object(payload.get("result"))
        next_page = result.get("nextPage")
        if isinstance(next_page, str) and next_page:
            if loop.time() + poll_seconds > deadline:
                raise GraphQueryError(
                    f"the graph query was still running after {int(deadline_seconds)} seconds; "
                    "narrow it with pattern filters, a tighter traversal or an aggregate"
                )
            await asyncio.sleep(poll_seconds)
            # The token is opaque: percent-encoded once, never decoded or inspected.
            target = f"{url}&continuationToken={quote(next_page, safe='')}"
            continue
        return _graph_evidence(payload)


class FabricGraphQueryService:
    def __init__(
        self,
        target: OntologyTarget,
        *,
        token_provider: FabricTokenProvider,
        client_factory: Any | None = None,
        metadata_cache: MetadataCache | None = None,
        capacity: CapacityMonitor | None = None,
    ) -> None:
        self._target = target
        self._token_provider = token_provider
        self._client_factory = client_factory or httpx.AsyncClient
        # One service instance serves every caller, so what it remembers about the
        # graph is held per principal: a remembered graph identity must never stand
        # in for the permission check that produced it.
        self._metadata = metadata_cache or MetadataCache()
        self._capacity = capacity

    @property
    def _source(self) -> str:
        return f"{self._target.workspace_id}/{self._target.ontology_id}"

    async def execute(self, partition: TaskPartition, query: str) -> str:
        return (await self.execute_evidence(partition, query)).preview

    async def execute_evidence(self, partition: TaskPartition, query: str) -> QueryEvidence:
        statement = validate_graph_statement(query)
        bearer_token = await self._token_provider.acquire(partition)
        if not bearer_token:
            raise GraphQueryError("Fabric owner token is required")
        async with self._client_factory(timeout=httpx.Timeout(60, connect=10), follow_redirects=False) as client:
            graph_model_id = await self._resolve_graph_model(client, bearer_token, partition)
            url = graph_query_url(self._target.workspace_id, graph_model_id)
            try:
                return await run_graph_query(
                    client, url=url, bearer_token=bearer_token, statement=statement, observe=self._observe_capacity
                )
            except GraphStatementError as rejected:
                repaired = repair_grouping(statement) if _GROUPING_FAILURE.search(str(rejected)) else None
                if repaired is None:
                    raise
                # One retry at the source costs a second or two; handing the slip back costs the model a whole turn.
                try:
                    evidence = await run_graph_query(
                        client, url=url, bearer_token=bearer_token, statement=repaired, observe=self._observe_capacity
                    )
                except GraphQueryError:
                    # The model has to fix what it wrote, so it gets the engine's reason for its own statement.
                    raise rejected from None
            return replace(evidence, executed_query=repaired)

    def _observe_capacity(self, response: httpx.Response) -> None:
        if self._capacity is None:
            return
        if response.status_code < 400:
            # Even a query the graph rejected was read by a running engine.
            self._capacity.record(CapacityState.ACTIVE)
        elif CAPACITY_NOT_ACTIVE in _failure_detail(response):
            self._capacity.record(CapacityState.PAUSED)

    async def _resolve_graph_model(self, client: Any, bearer_token: str, partition: TaskPartition) -> str:
        # Listing a workspace needs Workspace.Read.All, which is a great deal more than reading one
        # graph is worth, so a configured ID is the normal path and discovery only a convenience.
        if self._target.graph_model_id is not None:
            return str(self._target.graph_model_id)
        key = MetadataKey.for_principal(partition, source=self._source, kind="graph_model")
        return await self._metadata.get_or_load(key, lambda: self._discover_graph_model(client, bearer_token))

    async def _discover_graph_model(self, client: Any, bearer_token: str) -> str:
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
                return item_id
        # A caller that may see no graphs at all is a different problem from a naming mismatch.
        raise GraphQueryError(f"none of the {listed} graphs visible in this workspace match the ontology")


def _serialize(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _headers(bearer_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {bearer_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _retry_after(response: httpx.Response) -> float:
    headers = cast(object, getattr(response, "headers", None))
    raw = cast(Any, headers).get("Retry-After") if headers is not None else None
    try:
        seconds = float(raw) if raw is not None else 1.0
    except (TypeError, ValueError):
        seconds = 1.0
    return min(max(seconds, 0.0), MAX_RETRY_AFTER_SECONDS)


def _values(payload: object) -> list[dict[str, object]]:
    if not isinstance(payload, dict):
        return []
    raw = cast(dict[str, object], payload).get("value")
    if not isinstance(raw, list):
        return []
    return [cast(dict[str, object], item) for item in cast(list[object], raw) if isinstance(item, dict)]


def _object(value: object) -> dict[str, object]:
    return cast(dict[str, object], value) if isinstance(value, dict) else {}


def _payload(response: httpx.Response) -> dict[str, object]:
    try:
        payload = response.json()
    except ValueError:
        raise GraphQueryError("the graph returned a response this tool cannot read") from None
    if not isinstance(payload, dict):
        raise GraphQueryError("the graph returned a response this tool cannot read")
    return cast(dict[str, object], payload)


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


_ECHOED_QUERY = re.compile(r"^(MATCH|LET|OPTIONAL|RETURN|FILTER|ORDER|GROUP|LIMIT)\b", re.IGNORECASE)


def _compact_reason(reason: str) -> str:
    """The engine echoes the query and draws a caret under it, which would crowd the actual message out."""
    kept = [
        line.strip()
        for line in reason.splitlines()
        if line.strip() and not set(line.strip()) <= {"^"} and not _ECHOED_QUERY.match(line.strip())
    ]
    return " ".join(kept).removeprefix("error: ") or reason


def _status_codes(status: dict[str, object]) -> set[str]:
    codes = {status.get("code")}
    additional = status.get("additionalStatuses")
    if isinstance(additional, list):
        codes.update(_object(item).get("code") for item in cast(list[object], additional))
    return {code for code in codes if isinstance(code, str)}


def _graph_evidence(payload: dict[str, object]) -> QueryEvidence:
    status = _object(payload.get("status"))
    code = status.get("code")
    if code not in _ROW_STATUSES:
        cause = _object(status.get("cause") or payload.get("cause"))
        candidates = (cause.get("description"), status.get("message"), status.get("description"))
        reason = next((item for item in candidates if isinstance(item, str) and item), None)
        detail = _compact_reason(reason)[:MAX_FAILURE_DETAIL_CHARS] if reason else "the graph rejected the query"
        # 42000 is the engine's user-correctable class: the statement is wrong, not the service.
        raise GraphStatementError(detail) if code == GQL_USER_ERROR else GraphQueryError(detail)
    # Graph truncates past 64 MB and reports it as a warning, primary or additional; the rows are then partial.
    additional = payload.get("additionalStatuses")
    if isinstance(additional, list):
        status = {**status, "additionalStatuses": cast(list[object], additional)}
    truncated = GQL_WARNING in _status_codes(status)
    rows = _object(payload.get("result")).get("data")
    if rows is None:
        rows = []
    if not isinstance(rows, list):
        raise GraphQueryError("the graph returned a result this tool cannot read")
    return rows_evidence(cast(list[object], rows), max_chars=MAX_GRAPH_RESULT_CHARS, source_incomplete=truncated)


# What the engine says when a grouped RETURN projects a property instead of a LET variable,
# or when GROUP BY names a property expression directly.
_GROUPING_FAILURE = re.compile(r"neither part of the GROUP BY|mismatched input '\.'", re.IGNORECASE)
_AGGREGATE = re.compile(
    r"\b(?:count|sum|avg|min|max|collect_list|stddev_samp|stddev_pop|percentile_cont|percentile_disc)\s*\(",
    re.IGNORECASE,
)
_VARIABLE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PROPERTY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\.([A-Za-z_][A-Za-z0-9_]*)$")
_ALIASED = re.compile(r"^(.*\S)\s+AS\s+([A-Za-z_][A-Za-z0-9_]*)$", re.IGNORECASE | re.DOTALL)
_STAGE = re.compile(r"\bNEXT\b", re.IGNORECASE)
_RETURN = re.compile(r"\bRETURN\b", re.IGNORECASE)
_GROUP_BY = re.compile(r"\bGROUP\s+BY\b", re.IGNORECASE)
_AFTER_GROUPING = re.compile(r"\b(?:ORDER\s+BY|OFFSET|SKIP|LIMIT)\b", re.IGNORECASE)


def repair_grouping(statement: str) -> str | None:
    """The statement with each grouped value bound by LET first, or None when that cannot be done safely.

    Fabric's GQL groups only by variables: `RETURN n.Name AS k, count(*) AS c GROUP BY k`
    is refused, while `LET k = n.Name RETURN k, count(*) AS c GROUP BY k` runs. Binding the
    same expression to the same name changes neither the groups nor the values, so the
    repaired statement answers exactly what was asked. Anything less certain is left to
    the model: a projected value missing from GROUP BY, an unnamed expression, or a name
    that would collide.
    """
    top = _top_level(statement)
    cuts = [0, *(match.start() for match in _keywords(_STAGE, statement, top)), len(statement)]
    repaired: list[str] = []
    changed = False
    for start, end in pairwise(cuts):
        stage = statement[start:end]
        fixed = _repair_stage(stage, top[start:end])
        if fixed is None:
            return None
        changed = changed or fixed != stage
        repaired.append(fixed)
    return "".join(repaired) if changed else None


def _repair_stage(stage: str, top: list[bool]) -> str | None:
    returns = list(_keywords(_RETURN, stage, top))
    if not returns:
        return stage
    returned = returns[-1]
    grouping = next((match for match in _keywords(_GROUP_BY, stage, top) if match.start() > returned.end()), None)
    if grouping is None:
        return stage
    after = next((match for match in _keywords(_AFTER_GROUPING, stage, top) if match.start() > grouping.end()), None)
    keys_end = after.start() if after is not None else len(stage)
    projection = stage[returned.end() : grouping.start()]
    distinct = re.match(r"\s*DISTINCT\b", projection, re.IGNORECASE)
    if distinct is not None:
        projection = projection[distinct.end() :]
    items = _split_commas(projection)
    keys = [" ".join(key.split()) for key in _split_commas(stage[grouping.end() : keys_end])]
    if not items or not keys or not all(keys):
        return None
    taken = {key for key in keys if _VARIABLE.match(key)}
    bindings: list[str] = []
    projected: list[str] = []
    for item in items:
        aliased = _ALIASED.match(item)
        expression = " ".join((aliased.group(1) if aliased else item).split())
        name = aliased.group(2) if aliased else None
        if not expression or _AGGREGATE.search(expression) or (_VARIABLE.match(expression) and name is None):
            projected.append(item)
            continue
        if name is None:
            property_name = _PROPERTY.match(expression)
            if property_name is None:
                return None
            name = property_name.group(1)
            if name in taken:
                return None
        if name not in keys and expression not in keys:
            # Grouping by one more value would change the groups, so that stays the model's call.
            return None
        keys = [name if key == expression else key for key in keys]
        taken.add(name)
        bindings.append(f"{name} = {expression}")
        projected.append(name)
    if not bindings:
        return stage
    # Whatever followed the grouping keys stays as it was, including the space before a following NEXT.
    tail = f" {stage[after.start() :]}" if after is not None else stage[len(stage.rstrip()) :]
    return (
        f"{stage[: returned.start()]}LET {', '.join(bindings)} RETURN "
        f"{'DISTINCT ' if distinct else ''}{', '.join(projected)} GROUP BY {', '.join(keys)}{tail}"
    )


def _top_level(text: str) -> list[bool]:
    """Per character, whether it is outside string literals, delimited names and brackets."""
    flags: list[bool] = []
    depth = 0
    quote: str | None = None
    escaped = False
    for character in text:
        if quote is not None:
            flags.append(False)
            if escaped:
                escaped = False
            elif character == "\\" and quote != "`":
                escaped = True
            elif character == quote:
                quote = None
            continue
        if character in "'\"`":
            quote = character
            flags.append(False)
        elif character in "([{":
            depth += 1
            flags.append(False)
        elif character in ")]}":
            depth = max(0, depth - 1)
            flags.append(False)
        else:
            flags.append(depth == 0)
    return flags


def _keywords(pattern: re.Pattern[str], text: str, top: list[bool]) -> list[re.Match[str]]:
    # A property such as r.Return is not the RETURN keyword, so a preceding dot disqualifies a match.
    return [
        match
        for match in pattern.finditer(text)
        if top[match.start()] and (match.start() == 0 or text[match.start() - 1] != ".")
    ]


def _split_commas(text: str) -> list[str]:
    top = _top_level(text)
    parts: list[str] = []
    start = 0
    for index, character in enumerate(text):
        if character == "," and top[index]:
            parts.append(text[start:index].strip())
            start = index + 1
    parts.append(text[start:].strip())
    return parts
