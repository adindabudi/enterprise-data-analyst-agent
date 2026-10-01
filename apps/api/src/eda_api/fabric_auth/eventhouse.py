"""Direct KQL access to the time series an ontology binds to an Eventhouse.

Time-series properties are not in the graph. The ontology binds them to a KQL
table, and the schema snapshot names that table, its timestamp column and the
column that joins it back to its entity. The agent writes the KQL itself and this
module runs exactly that statement through the KQL database's Microsoft-hosted
remote MCP server (`executeQuery`) with the signed-in user's own Fabric token, so
Fabric enforces that user's access on every read.

`search_ontology` answered the same questions by rewriting them with a language
model: 9 to 31 seconds and about 2,400 CU-s a call, and it quietly turned a
latest reading into a maximum. A warm KQL query through this endpoint took about
two seconds in the lab.

Reads only: a management command, a reference to another database or cluster,
and a plugin that calls out of the database are refused before anything reaches
Fabric.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Mapping
from typing import cast

from eda_runtime_state.models import TaskPartition

from eda_api.fabric_ontology import OntologyTarget

from .capacity import CAPACITY_NOT_ACTIVE, CapacityMonitor, CapacityState
from .graph import FabricTokenProvider, QueryEvidence, rows_evidence
from .mcp_session import McpSession, McpSessionPool, SessionOpener
from .metadata_cache import MetadataCache, MetadataKey

MAX_KQL_QUERY_CHARS = 8_000
MAX_KQL_RESULT_CHARS = 8_000
# executeQuery requires maxRecords, caps it at 1,000 and says nothing when a result held more.
KQL_MAX_RECORDS = 1_000
KQL_QUERY_DEADLINE_SECONDS = 120.0
MAX_FAILURE_DETAIL_CHARS = 200
EXECUTE_TOOL = "executeQuery"
_EXECUTE_ARGUMENTS = frozenset({"kqlQuery", "maxRecords"})


class KqlQueryError(ValueError):
    pass


# String literals are blanked before the checks below, so a value can neither hide nor fake a keyword.
_KQL_STRINGS = re.compile(r"```.*?```|[hH]?@?'(?:[^'\\\n]|\\.)*'|[hH]?@?\"(?:[^\"\\\n]|\\.)*\"", re.DOTALL)
_CROSS_SCOPE = re.compile(r"\b(?:cluster|database|external_table)\s*\(|\bexternaldata\b", re.IGNORECASE)
_CALLOUT = re.compile(
    r"\b(?:http_request(?:_post)?|sql_request|mysql_request|postgresql_request|cosmosdb_sql_request"
    r"|azure_digital_twins_query_request)\b|\bevaluate\b[^|;]*?\b(?:python|r)\s*\(",
    re.IGNORECASE,
)
_PROVIDER_MESSAGE = re.compile(r'"@message"\s*:\s*"((?:[^"\\]|\\.)*)"')
_SCOPE_ASSIGNMENT = re.compile(r"\b(?:cluster|database)\s*=\s*'[^']*',?", re.IGNORECASE)
_LINK = re.compile(r"https?://\S+", re.IGNORECASE)
_IDENTIFIER = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE)


def validate_kql_statement(query: str) -> str:
    statement = query.strip()
    if not 3 <= len(statement) <= MAX_KQL_QUERY_CHARS:
        raise KqlQueryError(f"KQL query must contain 3-{MAX_KQL_QUERY_CHARS} characters")
    bare = _KQL_STRINGS.sub("''", statement)
    if any(part.lstrip().startswith(".") for part in re.split(r"[;\n]", bare)):
        raise KqlQueryError("KQL management commands are not allowed; write a read-only query")
    if _CROSS_SCOPE.search(bare):
        raise KqlQueryError(
            "KQL may read only the configured database; cluster(), database(), external tables "
            "and externaldata are not allowed"
        )
    if _CALLOUT.search(bare):
        raise KqlQueryError("KQL plugins that call out of the database are not allowed")
    return statement


class FabricEventhouseQueryService:
    def __init__(
        self,
        target: OntologyTarget,
        *,
        token_provider: FabricTokenProvider,
        session_opener: SessionOpener | None = None,
        metadata_cache: MetadataCache | None = None,
        capacity: CapacityMonitor | None = None,
        deadline_seconds: float = KQL_QUERY_DEADLINE_SECONDS,
    ) -> None:
        endpoint = target.kql_endpoint
        if endpoint is None:
            raise ValueError("the source has no KQL database configured")
        self._endpoint = endpoint
        self._token_provider = token_provider
        # A held session answers in about 1.6 s where a new one took 4 to 6; sessions stay with their token.
        self._sessions = McpSessionPool(session_opener)
        # The executeQuery contract belongs to the endpoint and carries no customer data,
        # so one confirmation serves every principal instead of a tool listing per query.
        self._metadata = metadata_cache or MetadataCache()
        self._capacity = capacity
        self._deadline_seconds = deadline_seconds

    async def aclose(self) -> None:
        await self._sessions.aclose()

    @property
    def _tool_contract_key(self) -> MetadataKey:
        return MetadataKey.for_endpoint(source=self._endpoint, kind="kql_tool_contract")

    async def execute(self, partition: TaskPartition, query: str) -> str:
        return (await self.execute_evidence(partition, query)).preview

    async def execute_evidence(self, partition: TaskPartition, query: str) -> QueryEvidence:
        statement = validate_kql_statement(query)
        bearer_token = await self._token_provider.acquire(partition)
        if not bearer_token:
            raise KqlQueryError("Fabric owner token is required")
        try:
            async with asyncio.timeout(self._deadline_seconds):
                result = await self._call(bearer_token, statement)
        except TimeoutError:
            # An unfinished query is an unknown outcome, never an empty result.
            raise KqlQueryError(
                f"the time-series query did not finish within {int(self._deadline_seconds)} seconds; "
                "aggregate or narrow it"
            ) from None
        return self._evidence(result)

    async def _call(self, bearer_token: str, statement: str) -> object:
        async def execute(session: McpSession) -> object:
            await self._confirm_tool_contract(session)
            return await session.call_tool(EXECUTE_TOOL, {"kqlQuery": statement, "maxRecords": KQL_MAX_RECORDS})

        try:
            return await self._sessions.run(self._endpoint, bearer_token, execute)
        except KqlQueryError:
            raise
        except Exception as error:
            if self._capacity is not None:
                self._capacity.observe_failure(error)
            raise

    async def _confirm_tool_contract(self, session: McpSession) -> None:
        async def validate() -> bool:
            _validate_execute_tool(await session.list_tools())
            return True

        await self._metadata.get_or_load(self._tool_contract_key, validate)

    def _evidence(self, result: object) -> QueryEvidence:
        payload = _json_object(_model_dump(result))
        text = _content_text(payload)
        if payload.get("isError") is True:
            if CAPACITY_NOT_ACTIVE in text and self._capacity is not None:
                self._capacity.record(CapacityState.PAUSED)
            raise KqlQueryError(kql_failure_detail(text))
        evidence = kql_evidence(payload, text)
        if self._capacity is not None:
            self._capacity.record(CapacityState.ACTIVE)
        return evidence


def kql_evidence(payload: Mapping[str, object], text: str) -> QueryEvidence:
    """Rows of the primary result, keyed by column; the endpoint answers in the Kusto v1 table shape."""
    structured = payload.get("structuredContent")
    if isinstance(structured, Mapping) and "Tables" in structured:
        document = _json_object(cast(object, structured))
    else:
        try:
            document = _json_object(json.loads(text))
        except (json.JSONDecodeError, ValueError):
            raise KqlQueryError("the KQL endpoint returned a result this tool cannot read") from None
    tables = [_json_object(table) for table in _object_list(document.get("Tables"))]
    primary = next((table for table in tables if table.get("TableName") == "PrimaryResult"), None)
    if primary is None and len(tables) == 1:
        primary = tables[0]
    if primary is None:
        raise KqlQueryError("the KQL endpoint returned no result table")
    columns = [_json_object(column).get("ColumnName") for column in _object_list(primary.get("Columns"))]
    if not all(isinstance(column, str) for column in columns):
        raise KqlQueryError("the KQL endpoint returned a result this tool cannot read")
    names = cast(list[str], columns)
    rows: list[object] = [
        dict(zip(names, cast(list[object], row), strict=False))
        for row in _object_list(primary.get("Rows"))
        if isinstance(row, list)
    ]
    return rows_evidence(rows, max_chars=MAX_KQL_RESULT_CHARS, source_incomplete=len(rows) >= KQL_MAX_RECORDS)


def kql_failure_detail(text: str) -> str:
    """The provider's own message, which the model needs to repair its query, without the source's address."""
    if CAPACITY_NOT_ACTIVE in text:
        return f"{CAPACITY_NOT_ACTIVE}: the source's Fabric capacity is not active"
    match = _PROVIDER_MESSAGE.search(text)
    message = text
    if match is not None:
        try:
            message = cast(str, json.loads(f'"{match.group(1)}"'))
        except json.JSONDecodeError:
            message = match.group(1)
    message = _SCOPE_ASSIGNMENT.sub("", message)
    message = _IDENTIFIER.sub("[id]", _LINK.sub("[link]", message))
    message = " ".join(message.split())
    return message[:MAX_FAILURE_DETAIL_CHARS] or "the KQL query failed"


def _validate_execute_tool(result: object) -> None:
    listed = _object_list(getattr(result, "tools", result))
    for raw_tool in listed:
        tool = _json_object(_model_dump(raw_tool))
        if tool.get("name") != EXECUTE_TOOL:
            continue
        schema = _json_object(tool.get("inputSchema") or {})
        properties = _json_object(schema.get("properties") or {})
        required = {item for item in _object_list(schema.get("required") or []) if isinstance(item, str)}
        query_type = _json_object(properties.get("kqlQuery") or {}).get("type")
        if query_type == "string" and "maxRecords" in properties and required <= _EXECUTE_ARGUMENTS:
            return
        break
    raise KqlQueryError("the KQL endpoint's executeQuery contract changed, so no query was sent")


def _content_text(payload: Mapping[str, object]) -> str:
    parts: list[str] = []
    for item in _object_list(payload.get("content") or []):
        text = _json_object(_model_dump(item)).get("text")
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts)


def _model_dump(value: object) -> object:
    model_dump = getattr(value, "model_dump", None)
    if not callable(model_dump):
        return value
    return model_dump(by_alias=True, exclude_none=True)


def _object_list(value: object) -> list[object]:
    if isinstance(value, list | tuple):
        return [*cast(list[object], value)]
    raise KqlQueryError("the KQL endpoint returned a result this tool cannot read")


def _json_object(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise KqlQueryError("the KQL endpoint returned a result this tool cannot read")
    return {str(key): item for key, item in cast(Mapping[object, object], value).items()}
