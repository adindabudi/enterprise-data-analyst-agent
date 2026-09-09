from __future__ import annotations

from dataclasses import dataclass

import pytest


@dataclass(frozen=True)
class FakeAgentResult:
    outcome: str
    applied_command_sequence: int


class DeterministicFakeAgent:
    async def run(self, *, pending_command_ids: tuple[str, ...] = ()) -> FakeAgentResult:
        return FakeAgentResult(outcome="continue", applied_command_sequence=len(pending_command_ids))


@pytest.fixture
def deterministic_fake_agent() -> DeterministicFakeAgent:
    return DeterministicFakeAgent()
