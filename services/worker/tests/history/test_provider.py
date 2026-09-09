from __future__ import annotations

from typing import cast
from uuid import UUID

import pytest
from agent_framework import AgentResponse, AgentSession, Content, Message, SessionContext, SupportsAgentRun
from eda_worker.history.models import ProjectionDocument, SessionPartition, TodoProjection
from eda_worker.history.provider import ProjectionCorruption, ProjectionHistoryProvider, ProjectionProvider
from eda_worker.history.repository import InMemoryProjectionRepository

PARTITION = SessionPartition(
    tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
    owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
    session_id="ses_1234567890abcdef",
)


@pytest.mark.asyncio
async def test_provider_persists_every_service_call_and_deduplicates() -> None:
    repository = InMemoryProjectionRepository()
    provider = ProjectionProvider(repository, PARTITION)
    context = [{"message_id": "msg_12345678", "role": "user", "text": "Synthetic revenue"}]

    await provider.after_run(context)
    await provider.after_run(context)

    projection = await repository.load_projection(PARTITION)
    assert projection is not None
    ids = [message["message_id"] for message in projection.messages]
    assert len(ids) == len(set(ids))


@pytest.mark.asyncio
async def test_reasoning_is_removed_before_projection_write() -> None:
    repository = InMemoryProjectionRepository()
    provider = ProjectionProvider(repository, PARTITION)
    context = [
        {
            "message_id": "msg_12345678",
            "role": "assistant",
            "text": "visible",
            "text_reasoning": "hidden",
            "protected_data": "secret",
        }
    ]

    await provider.after_run(context)

    projection = await repository.load_projection(PARTITION)
    assert projection is not None
    assert "text_reasoning" not in str(projection.messages)
    assert "protected_data" not in str(projection.messages)


@pytest.mark.asyncio
async def test_history_provider_persists_and_restores_each_service_call() -> None:
    repository = InMemoryProjectionRepository()
    provider = ProjectionHistoryProvider(repository)
    session = AgentSession(session_id=PARTITION.session_id)
    session.state[provider.source_id] = {"partition": PARTITION.model_dump(mode="json")}
    context = SessionContext(
        session_id=session.session_id,
        input_messages=[Message(role="user", contents=["Inspect the artifact."])],
    )
    context._response = AgentResponse(  # pyright: ignore[reportPrivateUsage]
        messages=[
            Message(
                role="assistant",
                contents=[Content.from_text_reasoning(text="private chain of thought"), "phase complete"],
            )
        ]
    )

    await provider.after_run(
        agent=cast(SupportsAgentRun, object()),
        session=session,
        context=context,
        state=session.state[provider.source_id],
    )

    projection = await repository.load_projection(PARTITION)
    assert projection is not None
    assert len(projection.messages) == 2
    assert "private chain of thought" not in str(projection.model_dump(mode="json"))
    restored = await provider.restore_session(PARTITION)
    assert restored.session_id == PARTITION.session_id
    assert restored.service_session_id is None
    assert [
        message.text
        for message in await provider.get_messages(restored.session_id, state=restored.state[provider.source_id])
    ] == [
        "Inspect the artifact.",
        "phase complete",
    ]


class RecordingRepository(InMemoryProjectionRepository):
    def __init__(self) -> None:
        super().__init__()
        self.todos: TodoProjection | None = None

    async def save_projection(
        self,
        partition: SessionPartition,
        projection: ProjectionDocument,
        todos: TodoProjection,
        expected_etag: str | None,
    ) -> ProjectionDocument:
        saved = await super().save_projection(partition, projection, todos, expected_etag)
        self.todos = todos
        return saved


async def _persist(provider: ProjectionHistoryProvider, session: AgentSession, reply: Message) -> None:
    context = SessionContext(
        session_id=session.session_id,
        input_messages=[Message(role="user", contents=["Which ICUs are above 75%?"], message_id="msg_12345678")],
    )
    context._response = AgentResponse(messages=[reply])  # pyright: ignore[reportPrivateUsage]
    await provider.after_run(
        agent=cast(SupportsAgentRun, object()),
        session=session,
        context=context,
        state=session.state[provider.source_id],
    )


def _session(provider: ProjectionHistoryProvider, todo: str | None = None) -> AgentSession:
    session = AgentSession(session_id=PARTITION.session_id)
    session.state[provider.source_id] = {"partition": PARTITION.model_dump(mode="json")}
    if todo is not None:
        session.state["todo"] = {"items": [{"id": 0, "title": todo, "isComplete": False}]}
    return session


@pytest.mark.asyncio
async def test_a_retried_turn_does_not_corrupt_the_projection() -> None:
    repository = InMemoryProjectionRepository()
    provider = ProjectionHistoryProvider(repository)
    session = _session(provider, todo="Pull the 38 patients")

    # agent_framework labels an unidentified message msg_<index>; a stream failure re-runs the turn,
    # so the same index carries different text on the second attempt.
    await _persist(provider, session, Message(role="assistant", contents=["partial"], message_id="msg_1"))
    await _persist(provider, session, Message(role="assistant", contents=["complete"], message_id="msg_1"))

    projection = await repository.load_projection(PARTITION)
    assert projection is not None
    assert "complete" in str(projection.messages)


@pytest.mark.asyncio
async def test_the_todo_projection_survives_a_retried_turn() -> None:
    repository = RecordingRepository()
    provider = ProjectionHistoryProvider(repository)
    session = _session(provider, todo="Pull the 38 patients")

    await _persist(provider, session, Message(role="assistant", contents=["partial"], message_id="msg_1"))
    await _persist(provider, session, Message(role="assistant", contents=["complete"], message_id="msg_1"))

    # The message and todo projections are written by one call, so a raise here empties the To-do panel.
    assert repository.todos is not None
    assert [item["title"] for item in repository.todos.items] == ["Pull the 38 patients"]


@pytest.mark.asyncio
async def test_a_stable_id_carrying_two_bodies_is_still_rejected() -> None:
    repository = InMemoryProjectionRepository()
    provider = ProjectionHistoryProvider(repository)
    session = _session(provider)

    await _persist(provider, session, Message(role="assistant", contents=["first"], message_id="msg_87654321"))
    with pytest.raises(ProjectionCorruption):
        await _persist(provider, session, Message(role="assistant", contents=["second"], message_id="msg_87654321"))
