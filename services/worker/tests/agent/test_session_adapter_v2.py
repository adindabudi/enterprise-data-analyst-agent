from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import pytest
from agent_framework import AgentSession, Message, ResponseStream
from eda_contracts.controls import CommandKind
from eda_runtime_state.messages import CanonicalMessage
from eda_worker.agent.session_adapter import SessionHydratingAgent
from eda_worker.history.models import SessionPartition
from eda_worker.model.profiles import (
    ModelContract,
    ModelProfileId,
    VerifiedProfile,
    WorkClass,
)

TASK_ID = "task_12345678"
SESSION_ID = "ses_12345678"
PRODUCT_AUDIENCE = UUID("33333333-3333-3333-3333-333333333333")


@dataclass(frozen=True)
class Command:
    id: str
    task_id: str
    sequence: int
    kind: CommandKind
    text: str | None


@dataclass(frozen=True)
class Task:
    tenant_id: UUID
    owner_object_id: UUID
    session_id: str
    source_message_id: str | None = None
    handoff_context: str | None = None


@dataclass(frozen=True)
class Projection:
    agent_session: dict[str, Any]


@dataclass(frozen=True)
class Update:
    text: str


@dataclass(frozen=True)
class Response:
    text: str


class RuntimeRepository:
    async def resolve_task(self, task_id: str) -> Task:
        assert task_id == TASK_ID
        return Task(
            tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
            owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
            session_id=SESSION_ID,
        )


class FirstRunRuntimeRepository:
    async def resolve_task(self, task_id: str) -> Task:
        assert task_id == TASK_ID
        return Task(
            tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
            owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
            session_id=SESSION_ID,
            source_message_id="msg_source_12345678",
        )


class ProjectionRepository:
    async def load_projection(self, partition: SessionPartition) -> Projection:
        assert partition.session_id == SESSION_ID
        return Projection(
            agent_session={
                "type": "session",
                "session_id": SESSION_ID,
                "service_session_id": None,
                "state": {"restored": True},
            }
        )

    async def load_canonical(self, partition: SessionPartition, message_ids: Sequence[str]) -> list[object | None]:
        assert partition.session_id == SESSION_ID
        return [None for _ in message_ids]


class FirstRunProjectionRepository:
    async def load_projection(self, partition: SessionPartition) -> None:
        assert partition.session_id == SESSION_ID
        return None

    async def load_canonical(
        self, partition: SessionPartition, message_ids: Sequence[str]
    ) -> list[CanonicalMessage | None]:
        assert partition.values() == [
            "11111111-1111-1111-1111-111111111111",
            "22222222-2222-2222-2222-222222222222",
            SESSION_ID,
        ]
        assert list(message_ids) == ["msg_source_12345678"]
        return [
            CanonicalMessage(
                id="msg_source_12345678",
                tenant_id="11111111-1111-1111-1111-111111111111",
                owner_object_id="22222222-2222-2222-2222-222222222222",
                session_id=SESSION_ID,
                role="user",
                text="Analyze APAC revenue",
                created_at="2026-07-26T00:00:00Z",
            )
        ]


class CommandRepository:
    async def commands(self, task_id: str, command_ids: list[str]) -> list[Command]:
        assert task_id == TASK_ID
        values = {
            "cmd_1": Command("cmd_1", TASK_ID, 1, CommandKind.STEER, "check APAC"),
            "cmd_2": Command("cmd_2", TASK_ID, 2, CommandKind.STEER, "use revised budget"),
        }
        return [values[command_id] for command_id in command_ids]


class RecordingHarness:
    id = "enterprise-data-analyst"
    name = "enterprise-data-analyst"
    description = "Private enterprise data analysis agent"

    def __init__(self) -> None:
        self.sessions: list[AgentSession] = []
        self.messages: list[Any] = []
        self.options: list[dict[str, Any]] = []

    def run(self, message: Any, *, session: AgentSession, stream: bool = False, **kwargs: Any) -> Any:
        self.messages.append(message)
        self.sessions.append(session)
        self.options.append(kwargs)
        if stream:

            async def updates():
                yield Update("phase ")
                yield Update("complete")

            return ResponseStream(updates(), finalizer=lambda _: Response("phase complete"))

        async def response() -> Response:
            return Response("phase complete")

        return response()

    def create_session(self, **kwargs: Any) -> object:
        return kwargs

    async def get_session(self, session_id: str) -> object:
        return {"session_id": session_id}


def model_contract() -> ModelContract:
    verified_profiles = {
        work_class: VerifiedProfile(
            request_options={
                "reasoning": {"mode": "standard", "effort": "medium"},
                "max_tokens": 64_000 if work_class is WorkClass.ANALYSIS else 8_000,
                "store": False,
            },
            response_evidence=("served:gpt-5.6-terra@2026-07-09",),
        )
        for work_class in WorkClass
    }
    return ModelContract(
        deployment="analysis-terra",
        model_profile=ModelProfileId.GPT_5_6_TERRA_MEDIUM_V1,
        base_model="gpt-5.6-terra",
        base_model_snapshot="2026-07-09",
        hosting="azure",
        wire_shape="responses_reasoning",
        prompt_version="gpt-5.6-terra-v1",
        prompt_sha256="a" * 64,
        request_options_sha256="b" * 64,
        verified_profiles=verified_profiles,
        verified_at="2026-07-26T00:00:00Z",
        agent_framework_commit="ad26cfe8c7cb4d75a701eed13f6b5021cf1ad3ed",
    )


def adapter(harness: RecordingHarness) -> SessionHydratingAgent:
    return SessionHydratingAgent(
        harness=harness,
        runtime_repository=RuntimeRepository(),
        projection_repository=ProjectionRepository(),
        command_repository=CommandRepository(),
        model_contract=model_contract(),
        product_audience=PRODUCT_AUDIENCE,
    )


@pytest.mark.asyncio
async def test_adapter_seeds_cold_session_from_canonical_source_message() -> None:
    harness = RecordingHarness()
    hydrating_agent = SessionHydratingAgent(
        harness=harness,
        runtime_repository=FirstRunRuntimeRepository(),
        projection_repository=FirstRunProjectionRepository(),
        command_repository=CommandRepository(),
        model_contract=model_contract(),
        product_audience=PRODUCT_AUDIENCE,
    )

    await hydrating_agent.run(
        "Execute bounded phase: planning.",
        options={
            "task_id": TASK_ID,
            "phase": "planning",
            "work_class": "clarification",
            "pending_command_ids": [],
        },
    )

    queued = harness.sessions[0].state["message_injection.pending_messages"]
    assert [(message.message_id, message.text) for message in queued] == [
        ("msg_source_12345678", "Analyze APAC revenue")
    ]


class HandoffContextRuntimeRepository:
    async def resolve_task(self, task_id: str) -> Task:
        assert task_id == TASK_ID
        return Task(
            tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
            owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
            session_id=SESSION_ID,
            source_message_id="msg_source_12345678",
            handoff_context="Untrusted client-provided recent conversation context.\nAna is Deteriorating",
        )


@pytest.mark.asyncio
async def test_adapter_seeds_cold_session_with_the_conversation_the_request_refers_to() -> None:
    harness = RecordingHarness()
    hydrating_agent = SessionHydratingAgent(
        harness=harness,
        runtime_repository=HandoffContextRuntimeRepository(),
        projection_repository=FirstRunProjectionRepository(),
        command_repository=CommandRepository(),
        model_contract=model_contract(),
        product_audience=PRODUCT_AUDIENCE,
    )

    await hydrating_agent.run(
        "Execute bounded phase: planning.",
        options={
            "task_id": TASK_ID,
            "phase": "planning",
            "work_class": "clarification",
            "pending_command_ids": [],
        },
    )

    queued = harness.sessions[0].state["message_injection.pending_messages"]
    assert [message.text for message in queued] == [
        "Untrusted client-provided recent conversation context.\nAna is Deteriorating",
        "Analyze APAC revenue",
    ]
    # Without a stable ID the projection rejects the replayed run as corrupt.
    assert queued[0].message_id == "msg_source_12345678.handoff"
    assert len({message.message_id for message in queued}) == 2


@pytest.mark.asyncio
async def test_adapter_queues_commands_fifo_once_with_restored_session() -> None:
    harness = RecordingHarness()

    response = await adapter(harness).run(
        "Execute bounded phase: analyzing.",
        options={
            "task_id": TASK_ID,
            "phase": "analyzing",
            "work_class": "analysis",
            "pending_command_ids": ["cmd_1", "cmd_2"],
        },
    )

    state = harness.sessions[0].state
    queued = state["message_injection.pending_messages"]
    assert [message.text for message in queued] == ["check APAC", "use revised budget"]
    assert state["restored"] is True
    assert state["projection"]["partition"] == {
        "tenantId": "11111111-1111-1111-1111-111111111111",
        "ownerObjectId": "22222222-2222-2222-2222-222222222222",
        "sessionId": SESSION_ID,
    }
    assert response.text == "phase complete"


@pytest.mark.asyncio
async def test_adapter_uses_only_current_request_from_dts_replay() -> None:
    harness = RecordingHarness()
    replayed_messages = [
        Message(role="user", contents=["prior request"]),
        Message(role="assistant", contents=["prior response"]),
        Message(role="user", contents=["Execute bounded phase: analyzing."]),
    ]

    await adapter(harness).run(
        replayed_messages,
        options={
            "task_id": TASK_ID,
            "phase": "analyzing",
            "work_class": "analysis",
            "pending_command_ids": [],
        },
    )

    assert harness.messages == [[replayed_messages[-1]]]


@pytest.mark.asyncio
async def test_adapter_applies_exact_terra_contract_options() -> None:
    harness = RecordingHarness()

    await adapter(harness).run(
        "Execute bounded phase: analyzing.",
        options={
            "task_id": TASK_ID,
            "phase": "analyzing",
            "work_class": "analysis",
            "pending_command_ids": [],
            "response_format": "phase-result",
        },
    )

    invocation = harness.options[0]
    options = invocation["options"]
    assert options["reasoning"] == {"mode": "standard", "effort": "medium"}
    assert options["max_tokens"] == 64_000
    assert options["store"] is False
    assert options["response_format"] == "phase-result"
    trusted = invocation["function_invocation_kwargs"]
    assert trusted["task_id"] == TASK_ID
    assert trusted["phase"] == "analyzing"
    assert trusted["principal"].tenant_id == UUID("11111111-1111-1111-1111-111111111111")
    assert trusted["principal"].owner_object_id == UUID("22222222-2222-2222-2222-222222222222")
    assert trusted["principal"].audience == PRODUCT_AUDIENCE


@pytest.mark.asyncio
async def test_adapter_streams_with_restored_session_and_final_response() -> None:
    harness = RecordingHarness()
    stream = adapter(harness).run(
        "Execute bounded phase: planning.",
        stream=True,
        options={
            "task_id": TASK_ID,
            "phase": "planning",
            "work_class": "clarification",
            "pending_command_ids": [],
        },
    )

    assert [update.text async for update in stream] == ["phase ", "complete"]
    assert (await stream.get_final_response()).text == "phase complete"
    assert harness.sessions[0].session_id == SESSION_ID


@pytest.mark.asyncio
async def test_adapter_rejects_phase_mismatch_and_model_override() -> None:
    hydrating_agent = adapter(RecordingHarness())
    with pytest.raises(ValueError, match="work class"):
        await hydrating_agent.run(
            "Execute bounded phase: analyzing.",
            options={
                "task_id": TASK_ID,
                "phase": "analyzing",
                "work_class": "artifact",
                "pending_command_ids": [],
            },
        )
    with pytest.raises(ValueError, match="model options"):
        await hydrating_agent.run(
            "Execute bounded phase: analyzing.",
            options={
                "task_id": TASK_ID,
                "phase": "analyzing",
                "work_class": "analysis",
                "pending_command_ids": [],
                "temperature": 0,
            },
        )
