"""What users see from the task lifecycle on cancellation, steering, repair, and failure."""

from __future__ import annotations

import asyncio

import pytest
from eda_contracts.tasks import TaskStatus
from eda_worker.lifecycle import (
    MAX_OUTPUT_REPAIR_ROUNDS,
    MAX_STEERING_ROUNDS,
    OutputRepairRequired,
    OwnershipLost,
    run_task_lifecycle,
)

from .lifecycle_fakes import FakeAnalysisServices, ModelPermissionError, control


def _kinds(services: FakeAnalysisServices) -> list[object]:
    return [call[0] for call in services.calls]


@pytest.mark.asyncio
async def test_a_successful_run_makes_one_agent_call_and_completes() -> None:
    services = FakeAnalysisServices()

    result = await run_task_lifecycle(services, "task_12345678")

    assert result.status is TaskStatus.COMPLETED
    assert result.final_message_id == "msg_final123"
    assert _kinds(services).count("run_analysis") == 1
    assert ("checkpoint", "task_12345678", TaskStatus.ANALYZING, 0) in services.calls
    assert ("checkpoint", "task_12345678", TaskStatus.COMPLETED, 1) in services.calls


@pytest.mark.asyncio
async def test_a_missing_task_fails_without_writing() -> None:
    services = FakeAnalysisServices()

    result = await run_task_lifecycle(services, "task_missing01")

    assert result.status is TaskStatus.FAILED
    assert result.failure_code == "task_unavailable"
    assert "checkpoint" not in _kinds(services)


@pytest.mark.asyncio
async def test_a_terminal_task_is_reported_as_it_is_and_never_rerun() -> None:
    services = FakeAnalysisServices()
    services.record = services.record.model_copy(
        update={"status": TaskStatus.COMPLETED, "final_message_id": "msg_earlier01"}
    )

    result = await run_task_lifecycle(services, "task_12345678")

    assert result.status is TaskStatus.COMPLETED
    assert result.final_message_id == "msg_earlier01"
    assert "run_analysis" not in _kinds(services)


@pytest.mark.asyncio
async def test_cancellation_before_the_first_pass_is_recorded_as_cancelled() -> None:
    services = FakeAnalysisServices(controls=[control(cancelled=True)])

    result = await run_task_lifecycle(services, "task_12345678")

    assert result.status is TaskStatus.CANCELLED
    assert "run_analysis" not in _kinds(services)
    assert ("cancel", "task_12345678") in services.calls


@pytest.mark.asyncio
async def test_steering_during_a_pass_starts_another_pass() -> None:
    services = FakeAnalysisServices(
        controls=[
            control(),
            control(),
            control(commands=("cmd_steer0001",), sequence=1),
            control(commands=("cmd_steer0001",), sequence=1),
            control(sequence=1),
        ],
        responses=["first pass", "steered pass"],
    )

    result = await run_task_lifecycle(services, "task_12345678")

    assert result.status is TaskStatus.COMPLETED
    assert _kinds(services).count("run_analysis") == 2
    assert ("run_analysis", "task_12345678", ("cmd_steer0001",)) in services.calls


@pytest.mark.asyncio
async def test_steering_beyond_the_budget_fails_truthfully() -> None:
    steering = control(commands=("cmd_steer0001",), sequence=1)
    services = FakeAnalysisServices(controls=[control(), *([steering] * 20)], responses=["pass"] * 10)

    result = await run_task_lifecycle(services, "task_12345678")

    assert result.status is TaskStatus.FAILED
    assert result.failure_code == "steering_budget_exhausted"
    assert _kinds(services).count("run_analysis") == MAX_STEERING_ROUNDS + 1


@pytest.mark.asyncio
async def test_a_missing_requested_file_is_repaired_then_fails_when_the_budget_is_spent() -> None:
    class NeverPublishes(FakeAnalysisServices):
        async def complete(self, task_id: str, text: str) -> str:
            self.calls.append(("complete", task_id, text))
            raise OutputRepairRequired("missing-required-outputs:xlsx")

    services = NeverPublishes(responses=["pass"] * 10)

    result = await run_task_lifecycle(services, "task_12345678")

    assert result.status is TaskStatus.FAILED
    assert result.failure_code == "output_repair_budget_exhausted"
    assert _kinds(services).count("run_analysis") == MAX_OUTPUT_REPAIR_ROUNDS + 1
    assert ("repair_feedback", "missing-required-outputs:xlsx") in services.calls


@pytest.mark.asyncio
async def test_a_provider_error_becomes_a_bounded_failure_code() -> None:
    services = FakeAnalysisServices(analysis_error=ModelPermissionError("denied"))

    result = await run_task_lifecycle(services, "task_12345678")

    assert result.status is TaskStatus.FAILED
    assert result.failure_code == "analysis_failed_modelpermissionerror_http_403_forbidden"


@pytest.mark.asyncio
async def test_an_empty_answer_is_a_failure_not_a_success() -> None:
    services = FakeAnalysisServices(responses=["   "])

    result = await run_task_lifecycle(services, "task_12345678")

    assert result.status is TaskStatus.FAILED
    assert result.failure_code == "empty_agent_response"


@pytest.mark.asyncio
async def test_losing_ownership_stops_before_anything_is_published() -> None:
    services = FakeAnalysisServices()
    checks = 0

    async def ensure_owner() -> None:
        nonlocal checks
        checks += 1
        # Intake and the pass start succeed; the lease is gone by publication time.
        if checks >= 3:
            raise OwnershipLost("lease generation changed")

    with pytest.raises(OwnershipLost):
        await run_task_lifecycle(services, "task_12345678", ensure_owner=ensure_owner)

    assert "complete" not in _kinds(services)
    assert ("checkpoint", "task_12345678", TaskStatus.COMPLETED, 1) not in services.calls
    assert "fail" not in _kinds(services)


@pytest.mark.asyncio
async def test_a_shutdown_cancellation_leaves_the_task_recoverable() -> None:
    started = asyncio.Event()

    class SlowServices(FakeAnalysisServices):
        async def run_analysis(self, task_id, pending_command_ids, repair_feedback=None):  # type: ignore[override]
            started.set()
            await asyncio.sleep(3600)
            return "never"

    services = SlowServices()
    running = asyncio.create_task(run_task_lifecycle(services, "task_12345678"))
    await started.wait()
    running.cancel()

    with pytest.raises(asyncio.CancelledError):
        await running
    # No user cancellation was requested, so the task is neither failed nor cancelled.
    assert "cancel" not in _kinds(services)
    assert "fail" not in _kinds(services)
    assert services.record.status is TaskStatus.ANALYZING


@pytest.mark.asyncio
async def test_a_user_cancellation_that_interrupts_a_pass_is_recorded_as_cancelled() -> None:
    started = asyncio.Event()

    class SlowServices(FakeAnalysisServices):
        async def run_analysis(self, task_id, pending_command_ids, repair_feedback=None):  # type: ignore[override]
            started.set()
            await asyncio.sleep(3600)
            return "never"

    services = SlowServices()
    running = asyncio.create_task(run_task_lifecycle(services, "task_12345678"))
    await started.wait()
    services.record = services.record.model_copy(update={"cancellation_requested": True})
    running.cancel()

    with pytest.raises(asyncio.CancelledError):
        await running
    assert ("cancel", "task_12345678") in services.calls
    assert services.record.status is TaskStatus.CANCELLED


@pytest.mark.asyncio
async def test_each_pass_is_announced_so_a_live_view_can_drop_the_text_it_replaces() -> None:
    services = FakeAnalysisServices(
        controls=[
            control(),
            control(),
            control(commands=("cmd_steer0001",), sequence=1),
            control(commands=("cmd_steer0001",), sequence=1),
            control(sequence=1),
        ],
        responses=["first", "second"],
    )
    passes: list[int] = []

    async def on_pass(index: int) -> None:
        passes.append(index)

    await run_task_lifecycle(services, "task_12345678", on_pass=on_pass)

    assert passes == [0, 1]
