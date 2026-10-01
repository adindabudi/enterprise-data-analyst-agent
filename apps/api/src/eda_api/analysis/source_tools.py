"""Source tools for the single analyst runtime.

The agent writes every query itself, from the schema snapshot pinned in its
instructions (see `source_context`), and these tools run exactly what it wrote:

* `query_graph` runs one GQL statement against the ontology's graph;
* `query_timeseries` runs one KQL query against the Eventhouse table that holds
  the ontology's time series, when the source has one.

There is no discovery tool and no natural-language route. Structure comes from
the snapshot, which the operator's job captured once; a question costs one query
and no model turn spent reading a schema. The tools are registered once for every
principal and carry no customer schema in their descriptions.

Every successful read keeps two products apart:

* the complete rows, stored as an immutable owner-scoped artifact and recorded
  on the task, so a workbook, chart or sandbox calculation reads everything the
  source returned; and
* a bounded preview for the model, marked when it had to be shortened.

Within one task an identical read reaches the source once, and a failure is
never stored as an answer.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import OrderedDict
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, cast

from agent_framework import FunctionInvocationContext, FunctionTool, tool
from eda_contracts import ArtifactRef
from eda_fabric_auth.msal_cache import FabricAuthorizationRequired
from eda_runtime_state.events import EventDraft
from eda_runtime_state.models import QueryResultRef, TaskPartition, TaskRecord
from pydantic import BaseModel, ConfigDict, Field

from eda_api.fabric_auth.eventhouse import KQL_MAX_RECORDS, MAX_KQL_QUERY_CHARS
from eda_api.fabric_auth.graph import MAX_GRAPH_QUERY_CHARS, QueryEvidence
from eda_api.fabric_auth.query_reuse import TaskQueryCoalescer, query_fingerprint

logger = logging.getLogger(__name__)

type SourceRoute = Literal["gql", "kql"]

MAX_STATUS_DETAIL_CHARS = 200
MAX_TRACKED_TASKS = 64
AUTHORIZATION_REQUIRED_DETAIL = (
    "The signed-in user has not linked Fabric access, so the source cannot be read in this task. "
    "Ask them to connect Fabric using the prompt shown in the conversation, then to ask again."
)
INCOMPLETE_RESULT_NOTE = (
    "The source returned only part of this result, so no total, ranking or complete list may be reported "
    "from it. Aggregate or narrow the query and read it again."
)
REPAIRED_QUERY_NOTE = (
    "The graph refused the grouping as written, so the source ran executedQuery instead: the same values, "
    "bound with LET before RETURN. The rows answer your query unchanged. Write later grouped queries that way."
)

GRAPH_QUERY_DESCRIPTION = (
    "Read the configured source by writing one read-only GQL query yourself, so the source runs exactly "
    "what you wrote and drops nothing. Use it for every number you intend to report over entities and "
    "relationships: counts, totals, ratios, thresholds, rankings and per-group breakdowns. The source's node "
    "labels, properties, stored values and relationships are in the schema snapshot in your instructions: "
    "write from it directly, and never query to rediscover what it lists. Every hop must name one listed "
    "relationship and follow its shown direction; an anonymous -[]-> hop is rejected even when its node "
    "labels exist. A plain traversal returns only rows present on every hop, so it cannot give you a "
    "denominator; use OPTIONAL MATCH for the hop that may be absent, then read the total from count(*) and "
    "the subset from count(alias) in the same query, rather than dividing one result by another. Filter on "
    "a stored value exactly as the snapshot lists it; for a property without a value list, match what the "
    "user wrote and report no match rather than guessing a similar value. This is ISO GQL, not Cypher. "
    "Filter inside the pattern, as in MATCH (n:EntityType WHERE n.PropertyName = 'x'), which prunes rows "
    "earlier than a separate FILTER statement. GROUP BY and ORDER BY come after RETURN, and LIMIT comes "
    "last. Bind every grouping value with LET before RETURN, then return that variable and list it in GROUP "
    "BY: LET k = n.PropertyName RETURN k, count(*) AS c GROUP BY k. Every RETURN item that is not an "
    "aggregate must be one of those GROUP BY variables, so RETURN n.PropertyName AS k ... GROUP BY k fails "
    "just as GROUP BY n.PropertyName does; that is the most common failure, and retrying the same shape "
    "wastes the call budget. Aggregates "
    "are count(*), count(x), count(DISTINCT x), sum, avg, min and max. CASE WHEN is not supported; get a "
    "conditional count from a filtered pattern or the OPTIONAL MATCH count(alias) form above. To keep only the "
    "groups whose aggregate passes a condition, as SQL HAVING does, end the grouped stage with NEXT and filter "
    "the next one: LET k = n.PropertyName RETURN k, count(*) AS c GROUP BY k NEXT FILTER c > 10 RETURN k, c. "
    "A FILTER placed directly after GROUP BY does not parse. Page with OFFSET n LIMIT m. A date or time "
    "property compares only against "
    "ZONED_DATETIME('2026-02-08T00:00:00Z') and never a bare string, while zoned_datetime() is the current "
    "instant. Subtracting two of them gives a duration, and sum, avg, min and max all accept a duration, so "
    "an age or an elapsed span is written as zoned_datetime() - n.SomeDateProperty; avg over the date itself "
    "is rejected. A duration literal is duration('P26W') or duration('P180D'), in weeks or days only: years "
    "and months are refused and a large day count overflows, so write a long span in weeks, such as "
    "duration('P3391W') for about 65 years, or compare the date with a ZONED_DATETIME literal. Many words "
    "are reserved, including "
    "label, edge, unit and value, so keep an alias short like v or c and rename it if a query fails to "
    "parse. Bound the result with LIMIT when a pattern could match a large number of rows, but never put "
    "LIMIT before an aggregate you report as a total. Returns status 'ok' with rows, where an empty list "
    "means nothing matched, or status 'error' with the engine's reason: repair the query from that reason "
    "instead of repeating it. Never state a value it did not return. When previewTruncated is true the rows "
    "are a preview: the complete result is stored as the `evidence` artifact, so compute over it with "
    "execute_in_sandbox instead of reporting totals from the preview. When sourceResultIncomplete is true "
    "the source itself returned only part of the rows."
)
TIMESERIES_QUERY_DESCRIPTION = (
    "Read the configured source's time series by writing one read-only KQL query yourself. Time-series "
    "columns are not in the graph: the schema snapshot in your instructions names each Eventhouse table, its "
    "time column, its value columns and the key column that joins a reading back to its entity. Aggregate "
    "in the query (summarize with count, avg, min, max or percentile; arg_max for the latest reading per key; "
    "bin() on the time column for a trend) instead of returning raw readings, and use take or top for "
    "bounded detail. Filter with where before summarize and project only the columns you need. ago() and "
    "now() count back from the current time, not from the data, so on historical readings they can return "
    "nothing: anchor a relative window on the data instead, as in let latest_reading = toscalar(T | "
    "summarize max(TimeColumn)); T | where TimeColumn >= startofday(latest_reading) - 2d. Cast a dynamic "
    "value with tostring(), tolong() or todouble() before grouping, joining or ordering on it. A join "
    "condition supports only equality, written $left.Key == $right.Key. prev(), next(), row_number() and "
    "row_cumsum() need ordered rows, so put order by or serialize before them. Words such as first and last "
    "are reserved as names; choose another alias or bracket it as ['last']. "
    "To describe the entity behind a reading, return its key column here and look the key "
    "up with query_graph. The query reads only this source's database: management commands, cluster(), "
    f"database(), external data and callout plugins are refused. At most {KQL_MAX_RECORDS:,} rows come back. "
    "Returns status 'ok' with rows, or status 'error' with the engine's reason: repair the query from that "
    "reason instead of repeating it. Never state a value it did not return. When previewTruncated is true "
    "the complete result is stored as the `evidence` artifact, so compute over it with execute_in_sandbox. "
    "When sourceResultIncomplete is true there were more rows than came back."
)

_IDENTIFIER = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE)
_SOURCE_LINK = re.compile(r"https?://\S+", re.IGNORECASE)
# Provider-internal tool names carry no business meaning, so in source text they can only steer.
_PROVIDER_TOOL = re.compile(
    r"execute_dax_query|search_ontology|list_ontology_entity_types|naturalLanguageResponse"
    r"|getSchema|getSpecificKQLExamples|getGeneralKQLExamples",
    re.IGNORECASE,
)


class TaskResolver(Protocol):
    async def resolve_task(self, task_id: str) -> TaskRecord | None: ...


class QueryResultLedger(Protocol):
    async def append_query_result(self, task_id: str, ref: QueryResultRef) -> bool: ...


class QueryResultWriter(Protocol):
    async def write(self, task: TaskRecord, display_name: str, content: bytes) -> ArtifactRef: ...


class EventSink(Protocol):
    async def append(self, draft: EventDraft) -> object: ...


class QuerySource(Protocol):
    async def execute_evidence(self, partition: TaskPartition, query: str) -> QueryEvidence: ...


class GraphQueryArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(
        min_length=3,
        max_length=MAX_GRAPH_QUERY_CHARS,
        description="One read-only ISO GQL statement that starts with MATCH or LET.",
    )


class TimeSeriesQueryArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(
        min_length=3,
        max_length=MAX_KQL_QUERY_CHARS,
        description="One read-only KQL query over a time-series table named in the schema snapshot.",
    )


class TaskCoalescers:
    """One exact-read coalescer per running task, bounded so finished tasks cannot accumulate."""

    def __init__(self, *, max_tasks: int = MAX_TRACKED_TASKS) -> None:
        self._max_tasks = max(1, max_tasks)
        self._coalescers: OrderedDict[str, TaskQueryCoalescer] = OrderedDict()

    def for_task(self, task_id: str) -> TaskQueryCoalescer:
        coalescer = self._coalescers.get(task_id)
        if coalescer is None:
            coalescer = TaskQueryCoalescer()
            self._coalescers[task_id] = coalescer
            while len(self._coalescers) > self._max_tasks:
                self._coalescers.popitem(last=False)
        else:
            self._coalescers.move_to_end(task_id)
        return coalescer

    def discard(self, task_id: str) -> None:
        self._coalescers.pop(task_id, None)


class SourceTools:
    def __init__(
        self,
        *,
        alias: str,
        description: str,
        graph: QuerySource,
        timeseries: QuerySource | None,
        tasks: TaskResolver,
        ledger: QueryResultLedger,
        writer: QueryResultWriter,
        events: EventSink | None = None,
        coalescers: TaskCoalescers | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._alias = alias
        self._description = description
        self._graph = graph
        self._timeseries = timeseries
        self._tasks = tasks
        self._ledger = ledger
        self._writer = writer
        self._events = events
        self.coalescers = coalescers or TaskCoalescers()
        self._clock = clock or (lambda: datetime.now(UTC))

    @property
    def alias(self) -> str:
        return self._alias

    def tools(self) -> list[FunctionTool]:
        catalog = (
            f" This deployment exposes exactly one source, alias '{self.alias}': "
            f"{source_reference_text(self._description)}."
        )

        @tool(
            name="query_graph",
            description=GRAPH_QUERY_DESCRIPTION + catalog,
            schema=GraphQueryArguments,
            approval_mode="never_require",
            additional_properties={"side_effect": "external_read"},
        )
        async def query_graph(context: FunctionInvocationContext, **arguments: Any) -> dict[str, Any]:
            return await self.graph_query(_task_id(context), GraphQueryArguments.model_validate(arguments))

        registered: list[FunctionTool] = [query_graph]
        if self._timeseries is not None:

            @tool(
                name="query_timeseries",
                description=TIMESERIES_QUERY_DESCRIPTION + catalog,
                schema=TimeSeriesQueryArguments,
                approval_mode="never_require",
                additional_properties={"side_effect": "external_read"},
            )
            async def query_timeseries(context: FunctionInvocationContext, **arguments: Any) -> dict[str, Any]:
                return await self.timeseries_query(
                    _task_id(context), TimeSeriesQueryArguments.model_validate(arguments)
                )

            registered.append(query_timeseries)
        return registered

    async def graph_query(self, task_id: str, arguments: GraphQueryArguments) -> dict[str, Any]:
        return await self._read(task_id, route="gql", query=arguments.query.strip(), source=self._graph)

    async def timeseries_query(self, task_id: str, arguments: TimeSeriesQueryArguments) -> dict[str, Any]:
        if self._timeseries is None:
            return _error("the configured source has no time-series route")
        return await self._read(task_id, route="kql", query=arguments.query.strip(), source=self._timeseries)

    async def _read(self, task_id: str, *, route: SourceRoute, query: str, source: QuerySource) -> dict[str, Any]:
        task = await self._task(task_id)
        if task.cancellation_requested:
            return {"status": "cancelled", "detail": "Task cancellation was requested."}
        partition = task.partition()

        async def run() -> str:
            evidence = await source.execute_evidence(partition, query)
            if not evidence.preview:
                # Raised, not returned, so an outcome with no evidence is never stored as this task's answer.
                raise _EmptyQueryResult
            # Provenance names the statement the rows actually came from.
            executed = evidence.executed_query or query
            return json.dumps(await self._result(task, route=route, query=executed, evidence=evidence))

        fingerprint = query_fingerprint(partition, source=self.alias, route=route, query=query)
        try:
            outcome = await self.coalescers.for_task(task.id).run(fingerprint, run)
        except FabricAuthorizationRequired:
            return await self._authorization_required(task)
        except _EmptyQueryResult:
            return _error("the Fabric query returned nothing to read")
        except Exception as error:
            logger.exception("source read failed for task %s route=%s", task_id, route)
            # A parse or planning error is what the model needs to repair its own query.
            return _error(_status_detail(error))
        payload = cast(dict[str, Any], json.loads(outcome.rows))
        if outcome.reused:
            payload["reused"] = True
        return payload

    async def _result(
        self,
        task: TaskRecord,
        *,
        route: SourceRoute,
        query: str,
        evidence: QueryEvidence,
    ) -> dict[str, Any]:
        stored = await self._store(task, route=route, query=query, evidence=evidence)
        payload: dict[str, Any] = {
            "status": "ok",
            "rows": evidence.preview,
            "rowCount": evidence.row_count,
            "previewTruncated": evidence.preview_truncated,
        }
        if evidence.source_incomplete:
            payload["sourceResultIncomplete"] = True
            payload["incompleteNote"] = INCOMPLETE_RESULT_NOTE
        if evidence.executed_query is not None:
            payload["executedQuery"] = evidence.executed_query
            payload["repairNote"] = REPAIRED_QUERY_NOTE
        if stored is not None:
            ref, listed = stored
            payload["evidence"] = {
                "artifactId": ref.artifact_id,
                "version": ref.version,
                "kind": ref.kind.value,
                "sha256": ref.sha256,
            }
            if not listed:
                payload["evidenceNote"] = (
                    "This task already lists ten query results, so this one is not in queryResults; "
                    "pass the evidence reference above to execute_in_sandbox directly."
                )
        elif evidence.preview_truncated:
            payload["evidenceNote"] = (
                "The complete result could not be stored, so only this preview is available. "
                "Do not report totals over the preview; narrow the query instead."
            )
        return payload

    async def _store(
        self,
        task: TaskRecord,
        *,
        route: SourceRoute,
        query: str,
        evidence: QueryEvidence,
    ) -> tuple[ArtifactRef, bool] | None:
        content = evidence.complete.encode("utf-8")
        query_sha256 = hashlib.sha256(query.encode("utf-8")).hexdigest()
        display_name = f"{route}-{query_sha256[:12]}.json"
        try:
            ref = await self._writer.write(task, display_name, content)
        except Exception:
            # Empty, beyond the artifact bound, or storage refused it: the preview still answers,
            # but without stored evidence the model is told not to report totals from it.
            logger.warning("query evidence was not stored for task %s route=%s", task.id, route, exc_info=True)
            return None
        record = QueryResultRef(
            artifact_id=ref.artifact_id,
            version=ref.version,
            kind=ref.kind.value,
            sha256=ref.sha256,
            display_name=display_name,
            query=query[:MAX_GRAPH_QUERY_CHARS],
            query_sha256=query_sha256,
            row_count=evidence.row_count or 0,
            source_alias=self.alias,
            message_id=task.source_message_id,
            executed_at=self._clock(),
        )
        try:
            listed = await self._ledger.append_query_result(task.id, record)
        except Exception:
            # The artifact exists and the reference is returned; only the task's index of it is missing.
            logger.warning("query evidence was not indexed on task %s", task.id, exc_info=True)
            listed = False
        return ref, listed

    async def _task(self, task_id: str) -> TaskRecord:
        task = await self._tasks.resolve_task(task_id)
        if task is None:
            raise ValueError("tool invocation references an unavailable task")
        return task

    async def _authorization_required(self, task: TaskRecord) -> dict[str, Any]:
        if self._events is not None:
            try:
                await self._events.append(
                    EventDraft(
                        session_id=task.session_id,
                        task_id=task.id,
                        type="auth.required",
                        payload={"actionPath": "/api/fabric/auth/start"},
                    )
                )
            except Exception:
                # The prompt is a convenience; the returned status already tells the model and the user.
                logger.warning("auth.required event could not be delivered for task %s", task.id)
        return {"status": "authorization_required", "detail": AUTHORIZATION_REQUIRED_DETAIL}


def _task_id(context: FunctionInvocationContext) -> str:
    raw_kwargs = cast(object, getattr(context, "kwargs", {}))
    kwargs: dict[str, object] = cast(dict[str, object], raw_kwargs) if isinstance(raw_kwargs, dict) else {}
    task_id: object = kwargs.get("task_id")
    if not isinstance(task_id, str) or not task_id.startswith("task_"):
        raise ValueError("tool invocation is missing its trusted task scope")
    return task_id


class _EmptyQueryResult(RuntimeError):
    """The source completed without returning anything to read."""


def _error(detail: str) -> dict[str, Any]:
    return {"status": "error", "detail": detail[:MAX_STATUS_DETAIL_CHARS]}


def _status_detail(error: BaseException) -> str:
    """What a reader can act on, with nothing in it that identifies the source."""
    reason = _failure_reason(error)
    if "CapacityNotActive" in reason:
        return "The source's Fabric capacity is paused, so no query can run until it is resumed."
    return _SOURCE_LINK.sub("[link]", _IDENTIFIER.sub("[id]", reason))[:MAX_STATUS_DETAIL_CHARS]


def _failure_reason(error: BaseException) -> str:
    if isinstance(error, BaseExceptionGroup):
        group = cast(BaseExceptionGroup[BaseException], error)
        causes = [_failure_reason(inner) for inner in group.exceptions]
        return "; ".join(cause for cause in causes if cause) or str(group)
    return str(error) or type(error).__name__


def source_reference_text(value: str) -> str:
    """Source-authored text is reference data; links, identifiers and provider tool names are redacted."""
    redacted = _SOURCE_LINK.sub("[link]", _IDENTIFIER.sub("[id]", value))
    return _PROVIDER_TOOL.sub("[tool]", redacted)
