from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest
from agent_framework import AgentSession
from azure.core import MatchConditions
from azure.cosmos.exceptions import CosmosHttpResponseError, CosmosResourceNotFoundError
from eda_api.auth.models import Principal
from eda_api.chat.provenance import CosmosChatQueryStore, read_query_record, sha256
from eda_api.chat.service import FabricQueryUpdate, MafInteractiveChatService, _DataStepRecorder
from eda_api.chat.sessions import CosmosInteractiveSessionStore
from eda_api.storage.history import CosmosSessionHistoryReader
from eda_runtime_state.messages import CosmosMessageRepository
from eda_runtime_state.models import TaskPartition

PARTITION = TaskPartition(tenant_id=UUID(int=1), owner_object_id=UUID(int=2), session_id="ses_provenance_12345678")
PRINCIPAL = Principal(tenant_id=UUID(int=1), owner_object_id=UUID(int=2), audience=UUID(int=3))


class Workspace:
    def __init__(self, *, fail_start=False, fail_finish=False, conflict=False):
        self.documents = {}
        self.partitions = []
        self.fail_start = fail_start
        self.fail_finish = fail_finish
        self.conflict = conflict

    def key(self, body):
        return (body["tenantId"], body["ownerObjectId"], body["sessionId"], body["id"])

    async def create_item(self, *, body):
        if body["recordType"] == "chatQuery" and self.fail_start:
            raise ConnectionError("provenance write unavailable")
        if self.key(body) in self.documents:
            raise CosmosHttpResponseError(status_code=409)
        return await self.upsert_item(body=body)

    async def upsert_item(self, *, body):
        stored = json.loads(json.dumps(body))
        if body["recordType"] == "chatQuery":
            stored["_etag"] = "v1"
        self.documents[self.key(body)] = stored
        return stored

    async def read_item(self, *, item, partition_key):
        self.partitions.append(partition_key)
        try:
            return self.documents[(*partition_key, item)].copy()
        except KeyError:
            raise CosmosResourceNotFoundError(message="not found") from None

    async def replace_item(self, *, item, body, etag, match_condition):
        assert match_condition == MatchConditions.IfNotModified
        assert item == body["id"]
        if self.fail_finish:
            raise ConnectionError("provenance write unavailable")
        if self.conflict:
            self.conflict = False
            self.documents[self.key(body)]["_etag"] = "v2"
            raise CosmosHttpResponseError(status_code=412)
        assert etag == self.documents[self.key(body)]["_etag"]
        return await self.upsert_item(body=body)

    def query_items(self, *, query, partition_key, parameters=()):
        self.partitions.append(partition_key)

        async def records():
            for body in list(self.documents.values()):
                if list(self.key(body)[:3]) != partition_key:
                    continue
                if parameters and body.get("responseId") != parameters[0]["value"]:
                    continue
                yield body.copy()

        return records()

    def queries(self):
        return [body for body in self.documents.values() if body["recordType"] == "chatQuery"]


class Source:
    alias = "indonesia-upstream"
    description = "Configured source"

    def __init__(self, workspace, *, failure=False, blocked=False):
        self.workspace = workspace
        self.failure = failure
        self.blocked = blocked
        self.calls = []
        self.entered = asyncio.Event()

    async def schema(self, partition):
        return "Field(FieldId)"

    async def relationships(self, partition):
        return ""

    async def execute(self, partition, query):
        # The durable start exists before any result-bearing execution.
        assert any(body["step"]["state"] == "running" for body in self.workspace.queries())
        self.calls.append((partition, query))
        self.entered.set()
        if self.blocked:
            await asyncio.Event().wait()
        if self.failure:
            raise ValueError("query rejected")
        return '[{"value":42}]'

    async def stream(self, partition, question):
        rows = await self.execute(partition, question)
        yield FabricQueryUpdate(result=rows)


class Agent:
    def __init__(self, queries=(), *, kind="gql", fail_after=False, swallow=False):
        self.queries = queries
        self.kind = kind
        self.fail_after = fail_after
        self.swallow = swallow
        self.calls = 0
        self.results = []

    def create_session(self):
        return AgentSession(session_id="ses_provenance_agent")

    def run(self, messages, **kwargs):
        self.calls += 1
        tools = {item.name: item for item in kwargs["tools"]}

        async def updates():
            for query in self.queries:
                try:
                    if self.kind == "gql":
                        result = await tools["query_graph"].func(query=query)
                    else:
                        result = await tools["query_fabric"].func(source=Source.alias, question=query)
                    self.results.append(result)
                except Exception:
                    if not self.swallow:
                        raise
            if self.fail_after:
                raise RuntimeError("model response interrupted")
            yield SimpleNamespace(text="Answer mentioning Indonesia but containing no fabricated query.")

        return updates()


async def setup(workspace, agent, *, source=None):
    messages = CosmosMessageRepository(workspace)
    user = await messages.append_user(PARTITION, "Show the source data", "source-1")
    query = source or Source(workspace)
    service = MafInteractiveChatService(
        agent=agent,
        messages=messages,
        options={},
        fabric_query=query,
        graph_query=query,
        query_store=CosmosChatQueryStore(workspace),
        session_store=CosmosInteractiveSessionStore(workspace),
    )
    return service, user, query


async def run(service, user, key="answer-1"):
    return [
        update
        async for update in service.stream(partition=PARTITION, message_id=user.id, history=(), idempotency_key=key)
    ]


@pytest.mark.asyncio
async def test_first_session_history_reload_replay_and_more_than_ten_actual_invocations():
    workspace = Workspace(conflict=True)
    statements = tuple(f"  MATCH (n:Field) RETURN n LIMIT {index + 1}  \n" for index in range(12))
    agent = Agent(statements)
    service, user, source = await setup(workspace, agent)
    updates = await run(service, user)
    history = await CosmosSessionHistoryReader(workspace).read_history(PRINCIPAL, PARTITION.session_id)
    restored = await CosmosSessionHistoryReader(workspace).read_history(PRINCIPAL, PARTITION.session_id)
    assert restored == history
    user_view = next(message for message in history.messages if message.message_id == user.id)
    assert len(user_view.steps) == len(workspace.queries()) == len(source.calls) == 12
    assert all(not message.steps for message in history.messages if message.role == "assistant")
    live = [update.data for update in updates if update.event == "data_step" and update.data["state"] == "completed"]
    assert user_view.steps == live
    for step, raw_query in zip(user_view.steps, statements, strict=True):
        assert step["query"] == raw_query.strip()
        assert step["querySha256"] == sha256(raw_query.strip())
        assert step["source"] == Source.alias
        assert step["rowCount"] == "1"
        assert step["resultSha256"] == sha256('[{"value":42}]')
        assert step["startedAt"] <= step["finishedAt"]
        assert step["executedAt"] == step["startedAt"]
        assert all(isinstance(value, str) for value in step.values())
    assert all(body["ttl"] == 2592000 and "rows" not in body for body in workspace.queries())
    state = await CosmosInteractiveSessionStore(workspace).load(PARTITION)
    assert len(state["queryRuns"]) == 10
    assert state["queryRuns"][-1]["sourceAlias"] == Source.alias
    assert state["queryRuns"][-1]["kind"] == "gql"
    # Neither eviction of the handoff rows nor cache size limits remove durable proof.
    await CosmosInteractiveSessionStore(workspace).save(PARTITION, {"session": "x" * 512_000})
    assert (await CosmosSessionHistoryReader(workspace).read_history(PRINCIPAL, PARTITION.session_id)) == history
    replay = await run(service, user)
    assert [update.data for update in replay if update.event == "data_step"] == live
    assert [update.event for update in replay][-2:] == ["delta", "completed"]
    assert agent.calls == 1 and len(source.calls) == 12
    assert all(partition == PARTITION.values() for partition in workspace.partitions)


@pytest.mark.asyncio
async def test_new_questions_and_interrupted_query_failures_stay_attached_to_their_user():
    workspace = Workspace()
    service, first_user, source = await setup(workspace, Agent(("MATCH (n:Field) RETURN n",)))
    await run(service, first_user)
    second = await CosmosMessageRepository(workspace).append_user(PARTITION, "Try another query", "source-2")
    source.failure = True
    service._agent = Agent(("MATCH (n:Missing) RETURN n",), fail_after=True)
    assert (await run(service, second, "answer-2"))[-1].event == "failed"
    history = await CosmosSessionHistoryReader(workspace).read_history(PRINCIPAL, PARTITION.session_id)
    by_id = {message.message_id: message for message in history.messages}
    assert by_id[first_user.id].steps[0]["state"] == "completed"
    failed = by_id[second.id].steps[0]
    assert failed["state"] == "failed" and "rowCount" not in failed and "resultSha256" not in failed
    assert failed["detail"] == "query rejected"
    assert len([message for message in history.messages if message.role == "assistant"]) == 1
    assert len({body["step"]["stepId"] for body in workspace.queries()}) == 2
    for principal, session in [
        (PRINCIPAL.model_copy(update={"owner_object_id": UUID(int=4)}), PARTITION.session_id),
        (PRINCIPAL.model_copy(update={"tenant_id": UUID(int=4)}), PARTITION.session_id),
        (PRINCIPAL, "ses_other_12345678"),
    ]:
        hidden = await CosmosSessionHistoryReader(workspace).read_history(principal, session)
        assert not hidden.messages


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_start", [True, False])
async def test_durable_write_failure_stops_the_response_even_if_agent_swallows_exception(fail_start):
    workspace = Workspace(fail_start=fail_start, fail_finish=not fail_start)
    agent = Agent(("MATCH (n:Field) RETURN n",), swallow=True)
    service, user, source = await setup(workspace, agent)
    updates = await run(service, user)
    assert updates[-1].event == "failed"
    assert "provenance could not be saved" in updates[-1].data["message"]
    assert not agent.results
    assert len(source.calls) == (0 if fail_start else 1)
    assert not any(update.event == "completed" for update in updates)
    assert not any(body.get("role") == "assistant" for body in workspace.documents.values())
    assert not any(body["step"]["state"] == "completed" for body in workspace.queries())


@pytest.mark.asyncio
async def test_cancellation_is_failed_and_interrupted_retry_creates_a_distinct_invocation():
    workspace = Workspace()
    source = Source(workspace, blocked=True)
    service, user, _ = await setup(workspace, Agent(("MATCH (n:Field) RETURN n",)), source=source)
    task = asyncio.create_task(run(service, user))
    await source.entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    [cancelled] = workspace.queries()
    assert cancelled["step"]["state"] == "failed"
    source.blocked = False
    await run(service, user)
    assert len({body["id"] for body in workspace.queries()}) == 2
    assert {body["step"]["state"] for body in workspace.queries()} == {"failed", "completed"}


@pytest.mark.asyncio
async def test_terminal_records_cannot_be_downgraded_and_invalid_evidence_is_logged(caplog):
    workspace = Workspace()
    service, user, _ = await setup(workspace, Agent(("MATCH (n:Field) RETURN n",)))
    await run(service, user)
    [document] = workspace.queries()
    completed = read_query_record(document, PARTITION)
    failed_step = completed.step.model_copy(update={"state": "failed", "result_sha256": None, "row_count": None})
    assert await CosmosChatQueryStore(workspace).finish(completed.model_copy(update={"step": failed_step})) == completed
    document["step"]["querySha256"] = "0" * 64
    with pytest.raises(ValueError, match="hash does not match"):
        await CosmosSessionHistoryReader(workspace).read_history(PRINCIPAL, PARTITION.session_id)
    assert "invalid stored chat query provenance" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, True])
async def test_terminal_outcome_retry_preserves_first_finish_timestamp(failure):
    workspace = Workspace()
    service, user, _ = await setup(
        workspace, Agent(("MATCH (n:Field) RETURN n",)), source=Source(workspace, failure=failure)
    )
    await run(service, user)
    [document] = workspace.queries()
    original = read_query_record(document, PARTITION)
    store = CosmosChatQueryStore(workspace)
    assert await store.finish(original) == original
    retry = original.model_copy(
        update={
            "step": original.step.model_copy(update={"finished_at": original.step.finished_at + timedelta(seconds=1)})
        }
    )
    assert await store.finish(retry) == original
    assert workspace.queries() == [document]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "changed"),
    [
        (False, {"result_sha256": sha256('[{"value":99}]')}),
        (False, {"row_count": 2}),
        (False, {"detail": "Different completion evidence"}),
        (True, {"detail": "Different failure evidence"}),
    ],
)
async def test_same_terminal_state_rejects_conflicting_outcome_evidence(failure, changed):
    workspace = Workspace()
    service, user, _ = await setup(
        workspace, Agent(("MATCH (n:Field) RETURN n",)), source=Source(workspace, failure=failure)
    )
    await run(service, user)
    [document] = workspace.queries()
    original = read_query_record(document, PARTITION)
    conflicting = original.model_copy(update={"step": original.step.model_copy(update=changed)})
    with pytest.raises(ValueError, match="terminal outcome conflicts"):
        await CosmosChatQueryStore(workspace).finish(conflicting)
    assert workspace.queries() == [document]


@pytest.mark.asyncio
async def test_response_replay_is_owner_and_session_scoped_and_orphan_records_are_not_attached():
    workspace = Workspace()
    service, user, _ = await setup(workspace, Agent(("MATCH (n:Field) RETURN n",)))
    await run(service, user)
    [document] = workspace.queries()
    response_id = document["responseId"]
    store = CosmosChatQueryStore(workspace)
    assert len(await store.list_response(PARTITION, response_id)) == 1
    assert await store.list_response(PARTITION, "msg_different_response") == ()
    for partition in (
        PARTITION.model_copy(update={"owner_object_id": UUID(int=99)}),
        PARTITION.model_copy(update={"tenant_id": UUID(int=99)}),
        PARTITION.model_copy(update={"session_id": "ses_different_12345678"}),
    ):
        assert await store.list_response(partition, response_id) == ()
    document["sourceMessageId"] = response_id  # An owned assistant is not a source user.
    await CosmosInteractiveSessionStore(workspace).save(PARTITION, {"queryRuns": []})
    history = await CosmosSessionHistoryReader(workspace).read_history(PRINCIPAL, PARTITION.session_id)
    assert all(not message.steps for message in history.messages)


@pytest.mark.asyncio
async def test_duplicate_invocation_start_cannot_overwrite_its_record():
    workspace = Workspace()
    queue = asyncio.Queue()
    recorder = _DataStepRecorder(
        queue,
        store=CosmosChatQueryStore(workspace),
        partition=PARTITION,
        source_message_id="msg_source_12345678",
        response_id="msg_response_12345678",
    )
    step = await recorder.start(kind="gql", label="Graph", query="MATCH (n:Field) RETURN n", source=Source.alias)
    [document] = workspace.queries()
    running = read_query_record(document, PARTITION)
    await recorder.finish(step, "[]")
    with pytest.raises(CosmosHttpResponseError) as conflict:
        await CosmosChatQueryStore(workspace).start(running)
    assert conflict.value.status_code == 409
    assert workspace.queries()[0]["step"]["state"] == "completed"


@pytest.mark.asyncio
async def test_conversation_never_gets_queries_from_answer_text():
    workspace = Workspace()
    service, user, source = await setup(workspace, Agent())
    assert (await run(service, user))[-1].event == "completed"
    history = await CosmosSessionHistoryReader(workspace).read_history(PRINCIPAL, PARTITION.session_id)
    assert not source.calls and not workspace.queries()
    assert all(not message.steps for message in history.messages)


@pytest.mark.asyncio
async def test_ontology_questions_up_to_ten_thousand_characters_are_never_truncated():
    workspace = Workspace()
    question = "show Field " + "x" * 9989
    service, user, source = await setup(workspace, Agent((question,), kind="ontology_search"))
    assert (await run(service, user))[-1].event == "completed"
    [document] = workspace.queries()
    assert source.calls[0][1] == document["step"]["query"] == question
    assert document["step"]["querySha256"] == sha256(question)
    assert not document["step"]["queryTruncated"]


@pytest.mark.asyncio
async def test_oversized_refusal_hashes_full_attempt_and_never_reports_truncated_success():
    events = asyncio.Queue()
    recorder = _DataStepRecorder(events)
    query = "MATCH " + "x" * 8000
    step = await recorder.start(kind="gql", label="Query attempt", query=query, source=Source.alias)
    assert step.query_truncated and step.query_sha256 == sha256(query)
    with pytest.raises(ValueError, match="untruncated"):
        await recorder.finish(step, "[]")
    await recorder.fail(step, "query too long")
    assert (await events.get()).data["state"] == "running"
    assert (await events.get()).data["state"] == "failed"


@pytest.mark.asyncio
async def test_legacy_queries_are_honest_owner_bound_and_prefer_durable_evidence():
    workspace = Workspace()
    service, first, _ = await setup(workspace, Agent())
    await run(service, first)
    second = await CosmosMessageRepository(workspace).append_user(PARTITION, "Search the ontology", "source-2")
    state = {
        "queryRuns": [
            {"query": "  MATCH (n:Old) RETURN n  ", "rows": '[{"v":1}]', "sourceAlias": None, "messageId": first.id},
            {
                "query": "search time series",
                "rows": '{"Fields":["v"],"Value":[[1],[2]]}',
                "sourceAlias": "historical-source",
                "messageId": second.id,
            },
            {"query": "unbound question", "rows": "[]"},
            {"query": "other user's question", "rows": "[]", "messageId": "msg_not_owned"},
            {"query": "broken", "rows": None, "messageId": first.id},
        ]
    }
    await CosmosInteractiveSessionStore(workspace).save(PARTITION, state)
    history = await CosmosSessionHistoryReader(workspace).read_history(PRINCIPAL, PARTITION.session_id)
    first_steps = next(message.steps for message in history.messages if message.message_id == first.id)
    second_steps = next(message.steps for message in history.messages if message.message_id == second.id)
    assert first_steps[0]["source"] == "Source identity not recorded"
    assert first_steps[0]["kind"] == "gql"
    assert first_steps[0]["querySha256"] == sha256("MATCH (n:Old) RETURN n")
    assert second_steps[0]["source"] == "historical-source"
    assert second_steps[0]["kind"] == "ontology_search" and second_steps[0]["rowCount"] == "2"
    assert all(
        "executedAt" not in step and "startedAt" not in step and "finishedAt" not in step
        for step in first_steps + second_steps
    )
    assert sum(len(message.steps) for message in history.messages) == 2
    service._agent = Agent(("MATCH (n:Field) RETURN n",))
    await run(service, second, "answer-2")
    # Force the legacy copy to remain beside the new record, as in a mixed-version rollout.
    await CosmosInteractiveSessionStore(workspace).save(PARTITION, state)
    restored = await CosmosSessionHistoryReader(workspace).read_history(PRINCIPAL, PARTITION.session_id)
    current_steps = next(message.steps for message in restored.messages if message.message_id == second.id)
    assert len(current_steps) == 1 and current_steps[0]["source"] == Source.alias
    assert "provenance" not in current_steps[0]
