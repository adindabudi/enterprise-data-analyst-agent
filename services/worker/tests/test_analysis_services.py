"""Streaming analysis passes publish visible text and return the same final answer."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from agent_framework import AgentResponse, AgentResponseUpdate, Content, Message, ResponseStream
from eda_worker.analysis_services import AnalysisRuntimeProvider, RuntimeAnalysisServices


class StreamingAgent:
    def __init__(self, chunks: list[str], final: str) -> None:
        self._chunks = chunks
        self._final = final
        self.calls: list[dict[str, object]] = []

    def run(self, message: str, *, stream: bool, options: dict[str, object]) -> Any:
        self.calls.append({"message": message, "stream": stream, "options": options})
        if not stream:

            async def once() -> AgentResponse:
                return AgentResponse(messages=[Message(role="assistant", contents=[self._final])])

            return once()

        async def updates():
            for chunk in self._chunks:
                yield AgentResponseUpdate(contents=[Content.from_text(chunk)], role="assistant")

        final = AgentResponse(messages=[Message(role="assistant", contents=[self._final])])
        return ResponseStream(updates(), finalizer=lambda _updates: final)


class Planner:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def ensure(self, task_id: str, *, pending_command_ids: tuple[str, ...] = ()) -> None:
        del pending_command_ids
        self.calls.append(task_id)


def _services(agent: StreamingAgent, sink_factory: Any = None) -> RuntimeAnalysisServices:
    runtime = SimpleNamespace(
        repository=None,
        primary_agent=agent,
        activities=None,
        resources=SimpleNamespace(aclose=None),
        output_planner=Planner(),
    )

    async def factory(settings: object) -> Any:
        del settings
        return runtime

    return RuntimeAnalysisServices(
        AnalysisRuntimeProvider(object(), runtime_factory=factory), delta_sink_factory=sink_factory
    )


@pytest.mark.asyncio
async def test_a_streamed_pass_publishes_each_visible_chunk_and_returns_the_final_answer() -> None:
    agent = StreamingAgent(["Occupancy ", "is 82%."], "Occupancy is 82%.")
    published: list[str] = []

    async def sink(text: str) -> None:
        published.append(text)

    services = _services(agent, sink_factory=lambda task_id: sink)

    text = await services.run_analysis("task_12345678", ())

    assert text == "Occupancy is 82%."
    assert published == ["Occupancy ", "is 82%."]
    assert agent.calls[0]["stream"] is True


@pytest.mark.asyncio
async def test_without_a_sink_the_pass_runs_unstreamed() -> None:
    agent = StreamingAgent([], "Done.")
    services = _services(agent)

    assert await services.run_analysis("task_12345678", ()) == "Done."
    assert agent.calls[0]["stream"] is False


@pytest.mark.asyncio
async def test_the_trusted_task_scope_is_the_only_option_passed_to_the_agent() -> None:
    agent = StreamingAgent([], "Done.")
    services = _services(agent)

    await services.run_analysis("task_12345678", ("cmd_12345678",))

    assert agent.calls[0]["options"] == {
        "task_id": "task_12345678",
        "phase": "chat",
        "work_class": "analysis",
        "pending_command_ids": ["cmd_12345678"],
    }
