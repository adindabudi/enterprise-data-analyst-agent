"""Actual interactive tool invocations, independent of bounded agent/handoff state."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Literal, Protocol, Self, cast
from uuid import UUID

from azure.core import MatchConditions
from azure.cosmos.aio import ContainerProxy
from azure.cosmos.exceptions import CosmosHttpResponseError
from eda_runtime_state.models import TaskPartition
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

logger = logging.getLogger(__name__)
QUERY_TTL_SECONDS = 30 * 24 * 60 * 60
QUERY_LIMITS = {"gql": 8_000, "ontology_search": 10_000}


def _camel_case(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(word.title() for word in rest)


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def row_count(rows: str) -> int | None:
    try:
        parsed: object = json.loads(rows)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, list):
        return len(cast(list[object], parsed))
    if isinstance(parsed, dict):
        table = cast(dict[str, object], parsed)
        if isinstance(table.get("Fields"), list) and isinstance(table.get("Value"), list):
            return len(cast(list[object], table["Value"]))
    return None


class ChatQueryStep(BaseModel):
    model_config = ConfigDict(alias_generator=_camel_case, populate_by_name=True, extra="forbid", frozen=True)

    step_id: str = Field(min_length=1, max_length=256)
    kind: Literal["gql", "ontology_search"]
    label: str = Field(max_length=256)
    state: Literal["running", "completed", "failed"] = "running"
    query: str = Field(max_length=10_000)
    source: str = Field(max_length=256)
    query_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    query_truncated: bool = False
    started_at: AwareDatetime
    finished_at: AwareDatetime | None = None
    result_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    row_count: int | None = Field(default=None, ge=0)
    detail: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def validate_evidence(self) -> Self:
        if len(self.query) > QUERY_LIMITS[self.kind]:
            raise ValueError("query evidence exceeds the source query limit")
        if not self.query_truncated and sha256(self.query) != self.query_sha256:
            raise ValueError("query evidence hash does not match")
        if self.state == "running":
            if self.finished_at is not None or self.result_sha256 is not None or self.row_count is not None:
                raise ValueError("running query cannot contain result evidence")
        elif self.finished_at is None or self.finished_at < self.started_at:
            raise ValueError("terminal query needs an actual finish time")
        if self.state == "completed" and (self.result_sha256 is None or self.query_truncated):
            raise ValueError("completed query needs untruncated query and result evidence")
        if self.state == "failed" and (self.result_sha256 is not None or self.row_count is not None):
            raise ValueError("failed query cannot contain successful result evidence")
        return self

    def event_data(self) -> dict[str, str]:
        values = self.model_dump(mode="json", by_alias=True, exclude_none=True)
        values["executedAt"] = values["startedAt"]
        return {key: str(value).lower() if isinstance(value, bool) else str(value) for key, value in values.items()}


class ChatQueryRecord(BaseModel):
    model_config = ConfigDict(alias_generator=_camel_case, populate_by_name=True, extra="forbid", frozen=True)

    id: str
    record_type: Literal["chatQuery"] = "chatQuery"
    tenant_id: str
    owner_object_id: str
    session_id: str
    source_message_id: str = Field(min_length=1)
    response_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    ttl: int = Field(default=QUERY_TTL_SECONDS, ge=QUERY_TTL_SECONDS, le=QUERY_TTL_SECONDS)
    step: ChatQueryStep

    @model_validator(mode="after")
    def validate_id(self) -> Self:
        if self.id != f"chat-query:{self.step.step_id}":
            raise ValueError("query record ID does not match invocation")
        return self

    def belongs_to(self, partition: TaskPartition) -> bool:
        return [self.tenant_id, self.owner_object_id, self.session_id] == partition.values()


def read_query_record(document: dict[str, Any], partition: TaskPartition) -> ChatQueryRecord:
    try:
        record = ChatQueryRecord.model_validate(
            {key: value for key, value in document.items() if not key.startswith("_")}
        )
        if not record.belongs_to(partition):
            raise ValueError("query provenance partition mismatch")
        return record
    except ValueError:
        # Do not log the document: its query can contain business data.
        logger.error("invalid stored chat query provenance")
        raise


class ChatQueryStore(Protocol):
    async def start(self, record: ChatQueryRecord) -> None: ...

    async def finish(self, record: ChatQueryRecord) -> ChatQueryRecord: ...

    async def list_response(self, partition: TaskPartition, response_id: str) -> tuple[ChatQueryRecord, ...]: ...


class CosmosChatQueryStore:
    def __init__(self, workspace: ContainerProxy) -> None:
        self._workspace = workspace

    async def start(self, record: ChatQueryRecord) -> None:
        if record.step.state != "running":
            raise ValueError("query must start running")
        # Never upsert: an invocation ID collision must stop before executing the tool.
        await self._workspace.create_item(body=record.model_dump(mode="json", by_alias=True))

    async def finish(self, record: ChatQueryRecord) -> ChatQueryRecord:
        if record.step.state == "running":
            raise ValueError("query finish must be terminal")
        partition = TaskPartition(
            tenant_id=UUID(record.tenant_id), owner_object_id=UUID(record.owner_object_id), session_id=record.session_id
        )
        for _ in range(3):
            document = await self._workspace.read_item(item=record.id, partition_key=partition.values())
            current = read_query_record(document, partition)
            if current.model_dump(exclude={"step"}) != record.model_dump(exclude={"step"}) or current.step.model_dump(
                include={"step_id", "kind", "label", "query", "source", "query_sha256", "query_truncated", "started_at"}
            ) != record.step.model_dump(
                include={"step_id", "kind", "label", "query", "source", "query_sha256", "query_truncated", "started_at"}
            ):
                raise ValueError("query invocation binding changed")
            if current.step.state != "running":
                if current.step.state == record.step.state and current.step.model_dump(
                    exclude={"finished_at"}
                ) != record.step.model_dump(exclude={"finished_at"}):
                    raise ValueError("query terminal outcome conflicts with stored evidence")
                return current
            try:
                await self._workspace.replace_item(
                    item=record.id,
                    body=record.model_dump(mode="json", by_alias=True),
                    etag=document["_etag"],
                    match_condition=MatchConditions.IfNotModified,
                )
                return record
            except CosmosHttpResponseError as error:
                if error.status_code != 412:
                    raise
        raise RuntimeError("query provenance update conflicted repeatedly")

    async def list_response(self, partition: TaskPartition, response_id: str) -> tuple[ChatQueryRecord, ...]:
        documents = self._workspace.query_items(
            query="SELECT * FROM c WHERE c.recordType = 'chatQuery' AND c.responseId = @responseId",
            parameters=[{"name": "@responseId", "value": response_id}],
            partition_key=partition.values(),
        )
        records = [read_query_record(document, partition) async for document in documents]
        return tuple(sorted(records, key=lambda item: (item.step.started_at, item.sequence, item.id)))


def legacy_query_steps(state: object, owned_user_ids: set[str]) -> dict[str, list[dict[str, str]]]:
    """Old codec: alias present meant ontology_search; None meant graph (source was lost).

    New graph runs record their kind explicitly. No source item ID or execution time
    existed in the old codec, so neither can be recovered from session metadata.
    """
    if not isinstance(state, dict):
        return {}
    runs = cast(dict[str, object], state).get("queryRuns")
    if not isinstance(runs, list):
        return {}
    recovered: dict[str, list[dict[str, str]]] = {}
    for index, value in enumerate(cast(list[object], runs)):
        if not isinstance(value, dict):
            logger.warning("invalid legacy chat query provenance")
            continue
        run = cast(dict[str, object], value)
        message_id, query, rows, alias = (run.get(key) for key in ("messageId", "query", "rows", "sourceAlias"))
        if not isinstance(message_id, str) or message_id not in owned_user_ids:
            continue
        if (
            not isinstance(query, str)
            or not isinstance(rows, str)
            or (alias is not None and not isinstance(alias, str))
        ):
            logger.warning("invalid legacy chat query provenance")
            continue
        explicit_kind = run.get("kind")
        if explicit_kind is not None and explicit_kind not in ("gql", "ontology_search"):
            logger.warning("invalid legacy chat query provenance")
            continue
        kind = str(explicit_kind) if explicit_kind is not None else ("ontology_search" if alias is not None else "gql")
        statement = query.strip() if kind == "gql" else query
        if len(statement) > QUERY_LIMITS[kind]:
            logger.warning("oversized legacy chat query provenance")
            continue
        source = alias if isinstance(alias, str) else "Source identity not recorded"
        step = {
            "stepId": f"legacy:{message_id}:{index}:{sha256(statement)[:16]}",
            "kind": kind,
            "label": f"Recovered {'graph query' if kind == 'gql' else 'ontology search'}",
            "state": "completed",
            "query": statement,
            "source": source,
            "querySha256": sha256(statement),
            "resultSha256": sha256(rows),
            "provenance": "legacy",
            "detail": "Recovered stored query and result; execution time and source item IDs were not recorded.",
        }
        counted = row_count(rows)
        if counted is not None:
            step["rowCount"] = str(counted)
        recovered.setdefault(message_id, []).append(step)
    return recovered
