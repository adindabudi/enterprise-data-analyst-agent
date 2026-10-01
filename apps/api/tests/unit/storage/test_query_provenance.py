from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

import pytest
from eda_api.auth.models import Principal
from eda_api.storage.history import CosmosSessionHistoryReader, SessionHistory
from eda_api.storage.query_provenance import QUERY_TTL_SECONDS, read_query_record, sha256
from eda_runtime_state.messages import CanonicalMessage
from eda_runtime_state.models import TaskPartition

PRINCIPAL = Principal(tenant_id=UUID(int=1), owner_object_id=UUID(int=2), audience=UUID(int=3))
PARTITION = TaskPartition(tenant_id=UUID(int=1), owner_object_id=UUID(int=2), session_id="ses_provenance_12345678")
NOW = datetime(2026, 7, 1, 9, 0, tzinfo=UTC)
ROWS = '[{"value":1}]'


class StoredWorkspace:
    def __init__(self, records: list[dict[str, Any]]) -> None:
        self.records = records

    def query_items(self, *, query: str, partition_key: list[str]):
        assert "chatQuery" in query and "interactiveSession" in query

        async def records():
            for record in self.records:
                if [record["tenantId"], record["ownerObjectId"], record["sessionId"]] == partition_key:
                    yield record

        return records()


def message(message_id: str, role: Literal["user", "assistant"] = "user", *, seconds: int = 0) -> dict[str, Any]:
    return CanonicalMessage(
        id=message_id,
        tenant_id=str(PARTITION.tenant_id),
        owner_object_id=str(PARTITION.owner_object_id),
        session_id=PARTITION.session_id,
        role=role,
        text=f"{role} text",
        created_at=NOW + timedelta(seconds=seconds),
    ).model_dump(mode="json")


def stored_query(
    source_message_id: str,
    *,
    query: str = "MATCH (n:Field) RETURN n",
    kind: str = "gql",
    sequence: int = 1,
    owner: UUID = PARTITION.owner_object_id,
) -> dict[str, Any]:
    step_id = f"step_{source_message_id}_{sequence}"
    return {
        "id": f"chat-query:{step_id}",
        "recordType": "chatQuery",
        "tenantId": str(PARTITION.tenant_id),
        "ownerObjectId": str(owner),
        "sessionId": PARTITION.session_id,
        "sourceMessageId": source_message_id,
        "responseId": "msg_response_12345678",
        "sequence": sequence,
        "ttl": QUERY_TTL_SECONDS,
        "step": {
            "stepId": step_id,
            "kind": kind,
            "label": "Graph query",
            "state": "completed",
            "query": query,
            "source": "indonesia-upstream",
            "querySha256": sha256(query),
            "queryTruncated": False,
            "startedAt": NOW.isoformat(),
            "finishedAt": (NOW + timedelta(seconds=1)).isoformat(),
            "resultSha256": sha256(ROWS),
            "rowCount": 1,
        },
        # Cosmos system properties are not part of the record.
        "_etag": "v1",
        "_ts": 1,
    }


async def read(records: list[dict[str, Any]], principal: Principal = PRINCIPAL) -> SessionHistory:
    workspace: Any = StoredWorkspace(records)
    return await CosmosSessionHistoryReader(workspace).read_history(principal, PARTITION.session_id)


def steps_of(history: SessionHistory, message_id: str) -> list[dict[str, str]]:
    return next(entry.steps for entry in history.messages if entry.message_id == message_id)


@pytest.mark.asyncio
async def test_stored_queries_attach_only_to_the_owned_question_that_asked() -> None:
    history = await read(
        [
            message("msg_question_12345678"),
            message("msg_answer_12345678", "assistant", seconds=1),
            stored_query("msg_question_12345678"),
            # An assistant message is not a source question, and an unknown message is an orphan.
            stored_query("msg_answer_12345678"),
            stored_query("msg_missing_12345678"),
        ]
    )

    [step] = steps_of(history, "msg_question_12345678")
    assert step["source"] == "indonesia-upstream"
    assert step["state"] == "completed"
    assert step["rowCount"] == "1"
    assert step["queryTruncated"] == "false"
    assert step["executedAt"] == step["startedAt"]
    assert steps_of(history, "msg_answer_12345678") == []

    hidden = await read(
        [message("msg_question_12345678"), stored_query("msg_question_12345678")],
        PRINCIPAL.model_copy(update={"owner_object_id": UUID(int=99)}),
    )
    assert not hidden.messages


@pytest.mark.asyncio
async def test_tampered_query_evidence_is_rejected_without_logging_the_query(caplog: pytest.LogCaptureFixture) -> None:
    tampered = stored_query("msg_question_12345678", query="MATCH (n:Secret) RETURN n")
    tampered["step"]["querySha256"] = "0" * 64

    with caplog.at_level(logging.ERROR), pytest.raises(ValueError, match="hash does not match"):
        await read([message("msg_question_12345678"), tampered])

    assert "invalid stored chat query provenance" in caplog.text
    assert "Secret" not in caplog.text


def test_a_record_from_another_partition_is_rejected() -> None:
    document = stored_query("msg_question_12345678", owner=UUID(int=99))

    with pytest.raises(ValueError, match="partition mismatch"):
        read_query_record(document, PARTITION)


def test_ontology_searches_up_to_ten_thousand_characters_stay_valid() -> None:
    question = "show Field " + "x" * 9989
    record = read_query_record(stored_query("msg_question_12345678", query=question, kind="ontology_search"), PARTITION)
    assert record.step.query == question

    with pytest.raises(ValueError, match="source query limit"):
        read_query_record(stored_query("msg_question_12345678", query="MATCH " + "x" * 8000), PARTITION)


@pytest.mark.asyncio
async def test_legacy_session_queries_are_honest_owner_bound_and_prefer_durable_evidence() -> None:
    state = {
        "queryRuns": [
            {
                "query": "  MATCH (n:Old) RETURN n  ",
                "rows": '[{"v":1}]',
                "sourceAlias": None,
                "messageId": "msg_first_12345678",
            },
            {
                "query": "search time series",
                "rows": '{"Fields":["v"],"Value":[[1],[2]]}',
                "sourceAlias": "historical-source",
                "messageId": "msg_second_12345678",
            },
            {"query": "MATCH (n:Stale) RETURN n", "rows": "[]", "messageId": "msg_third_12345678"},
            {"query": "unbound question", "rows": "[]"},
            {"query": "other user's question", "rows": "[]", "messageId": "msg_not_owned_12345678"},
            {"query": "broken", "rows": None, "messageId": "msg_first_12345678"},
        ]
    }
    history = await read(
        [
            message("msg_first_12345678"),
            message("msg_second_12345678", seconds=1),
            message("msg_third_12345678", seconds=2),
            {
                "id": "interactive-session",
                "recordType": "interactiveSession",
                "tenantId": str(PARTITION.tenant_id),
                "ownerObjectId": str(PARTITION.owner_object_id),
                "sessionId": PARTITION.session_id,
                "ttl": QUERY_TTL_SECONDS,
                "state": state,
            },
            stored_query("msg_third_12345678"),
        ]
    )

    [first] = steps_of(history, "msg_first_12345678")
    assert first["source"] == "Source identity not recorded"
    assert first["kind"] == "gql"
    assert first["querySha256"] == sha256("MATCH (n:Old) RETURN n")
    assert first["provenance"] == "legacy"
    [second] = steps_of(history, "msg_second_12345678")
    assert second["source"] == "historical-source"
    assert second["kind"] == "ontology_search" and second["rowCount"] == "2"
    assert all("executedAt" not in step and "startedAt" not in step for step in (first, second))
    # A durable record replaces the legacy copy of the same question.
    [third] = steps_of(history, "msg_third_12345678")
    assert third["source"] == "indonesia-upstream" and "provenance" not in third
    assert sum(len(entry.steps) for entry in history.messages) == 3
