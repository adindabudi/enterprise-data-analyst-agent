from __future__ import annotations

import json
from collections import deque
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from agent_framework import Message
from eda_contracts.tasks import TaskStatus
from eda_runtime_state.models import TaskRecord
from eda_worker.contracts import ControlSnapshot
from eda_worker.hosted_workflow import MAX_STEERING_ROUNDS, build_hosted_workflow


class FakeAnalysisServices:
    def __init__(
        self,
        controls: list[ControlSnapshot] | None = None,
        responses: list[str] | None = None,
        analysis_error: Exception | None = None,
    ) -> None:
        now = datetime.now(UTC)
        self.record = TaskRecord(
            id="task_12345678",
            tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
            owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
            session_id="ses_1234567890abcdef",
            status=TaskStatus.PLANNING,
            checkpoint_sequence=0,
            command_sequence=0,
            applied_command_sequence=0,
            created_at=now,
            updated_at=now,
            expires_at=now + timedelta(days=1),
        )
        self._controls = deque(controls or [control()])
        self._last_control = self._controls[-1]
        self._responses = deque(responses or ["Analysis complete."])
        self._analysis_error = analysis_error
        self.calls: list[tuple[object, ...]] = []

    async def task(self, task_id: str) -> TaskRecord | None:
        self.calls.append(("task", task_id))
        return self.record if task_id == self.record.id else None

    async def controls(self, task_id: str) -> ControlSnapshot:
        self.calls.append(("controls", task_id))
        if self._controls:
            self._last_control = self._controls.popleft()
        return self._last_control

    async def checkpoint(self, task_id: str, status: TaskStatus, expected_checkpoint: int) -> TaskRecord:
        self.calls.append(("checkpoint", task_id, status, expected_checkpoint))
        assert expected_checkpoint == self.record.checkpoint_sequence
        self.record = self.record.model_copy(
            update={
                "status": status,
                "checkpoint_sequence": expected_checkpoint + 1,
            }
        )
        return self.record

    async def run_analysis(
        self,
        task_id: str,
        pending_command_ids: tuple[str, ...],
        repair_feedback: str | None = None,
    ) -> str:
        self.calls.append(("run_analysis", task_id, pending_command_ids))
        if repair_feedback is not None:
            self.calls.append(("repair_feedback", repair_feedback))
        if self._analysis_error is not None:
            raise self._analysis_error
        return self._responses.popleft()

    async def acknowledge(self, task_id: str, through_sequence: int) -> None:
        self.calls.append(("acknowledge", task_id, through_sequence))

    async def complete(self, task_id: str, text: str) -> str:
        self.calls.append(("complete", task_id, text))
        return "msg_final123"

    async def cancel(self, task_id: str) -> None:
        self.calls.append(("cancel", task_id))

    async def fail(self, task_id: str, failure_code: str) -> None:
        self.calls.append(("fail", task_id, failure_code))


class ModelPermissionError(RuntimeError):
    status_code = 403
    code = "forbidden"


def control(
    *,
    cancelled: bool = False,
    commands: tuple[str, ...] = (),
    sequence: int = 0,
) -> ControlSnapshot:
    return ControlSnapshot(
        cancellationRequested=cancelled,
        pendingCommandIds=commands,
        highestCommandSequence=sequence,
        authResumed=False,
    )


async def run_workflow(services: FakeAnalysisServices) -> dict[str, object]:
    workflow = build_hosted_workflow(services)
    result = await workflow.run([Message(role="user", contents=['{"taskId":"task_12345678"}'])])
    outputs = result.get_outputs()
    assert len(outputs) == 1
    return json.loads(outputs[0])


@pytest.mark.asyncio
async def test_successful_run_has_one_bounded_agent_call_and_domain_completion() -> None:
    services = FakeAnalysisServices(
        controls=[control(), control(), control()],
        responses=["Authoritative result."],
    )

    result = await run_workflow(services)

    assert result == {
        "taskId": "task_12345678",
        "status": "completed",
        "finalMessageId": "msg_final123",
        "failureCode": None,
    }
    assert [call[0] for call in services.calls].count("run_analysis") == 1
    assert ("checkpoint", "task_12345678", TaskStatus.ANALYZING, 0) in services.calls
    assert ("checkpoint", "task_12345678", TaskStatus.COMPLETED, 1) in services.calls


@pytest.mark.asyncio
async def test_new_steering_is_applied_by_a_bounded_graph_self_loop() -> None:
    services = FakeAnalysisServices(
        controls=[
            control(),
            control(),
            control(commands=("cmd_12345678",), sequence=1),
            control(commands=("cmd_12345678",), sequence=1),
            control(),
        ],
        responses=["First result.", "Steered result."],
    )

    result = await run_workflow(services)

    assert result["status"] == "completed"
    assert [call for call in services.calls if call[0] == "run_analysis"] == [
        ("run_analysis", "task_12345678", ()),
        ("run_analysis", "task_12345678", ("cmd_12345678",)),
    ]
    assert ("acknowledge", "task_12345678", 1) in services.calls
    assert ("complete", "task_12345678", "Steered result.") in services.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("failures, expected_status, expected_calls", [(1, "completed", 2), (3, "failed", 3)])
async def test_missing_outputs_use_bounded_repair(
    failures: int,
    expected_status: str,
    expected_calls: int,
) -> None:
    from eda_worker.hosted_workflow import OutputRepairRequired

    class IncompleteServices(FakeAnalysisServices):
        async def complete(self, task_id: str, text: str) -> str:
            self.calls.append(("complete", task_id, text))
            if sum(call[0] == "complete" for call in self.calls) <= failures:
                raise OutputRepairRequired("missing-required-outputs:xlsx(0/1)")
            return "msg_final123"

    services = IncompleteServices(responses=["Attempt one.", "Attempt two.", "Attempt three."])

    result = await run_workflow(services)

    assert result["status"] == expected_status
    assert sum(call[0] == "run_analysis" for call in services.calls) == expected_calls
    assert ("repair_feedback", "missing-required-outputs:xlsx(0/1)") in services.calls
    if expected_status == "failed":
        assert result["failureCode"] == "output_repair_budget_exhausted"
        assert ("fail", services.record.id, "output_repair_budget_exhausted") in services.calls


@pytest.mark.asyncio
async def test_steering_budget_exhaustion_fails_instead_of_looping_forever() -> None:
    snapshots = [control()]
    for sequence in range(1, MAX_STEERING_ROUNDS + 2):
        snapshots.extend(
            [
                control(commands=(f"cmd_{sequence:08d}",), sequence=sequence),
                control(commands=(f"cmd_{sequence:08d}",), sequence=sequence),
            ]
        )
    services = FakeAnalysisServices(
        controls=snapshots,
        responses=[f"result-{index}" for index in range(MAX_STEERING_ROUNDS + 1)],
    )

    result = await run_workflow(services)

    assert result["status"] == "failed"
    assert result["failureCode"] == "steering_budget_exhausted"
    assert [call[0] for call in services.calls].count("run_analysis") <= MAX_STEERING_ROUNDS + 1


@pytest.mark.asyncio
async def test_cancellation_before_analysis_never_calls_the_model() -> None:
    services = FakeAnalysisServices(controls=[control(cancelled=True)])

    result = await run_workflow(services)

    assert result["status"] == "cancelled"
    assert not any(call[0] == "run_analysis" for call in services.calls)
    assert ("cancel", "task_12345678") in services.calls


@pytest.mark.asyncio
async def test_empty_agent_response_becomes_a_terminal_domain_failure() -> None:
    services = FakeAnalysisServices(
        controls=[control(), control()],
        responses=["   "],
    )

    result = await run_workflow(services)

    assert result["status"] == "failed"
    assert result["failureCode"] == "empty_agent_response"
    assert ("fail", "task_12345678", "empty_agent_response") in services.calls


@pytest.mark.asyncio
async def test_analysis_exception_logs_task_context_before_domain_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    services = FakeAnalysisServices(
        controls=[control(), control()],
        analysis_error=RuntimeError("model access denied"),
    )

    result = await run_workflow(services)

    assert result["failureCode"] == "analysis_failed_runtimeerror"
    assert "analysis execution failed" in caplog.text
    assert "task_12345678" in caplog.text
    assert "model access denied" in caplog.text


@pytest.mark.asyncio
async def test_analysis_failure_code_captures_safe_provider_metadata() -> None:
    services = FakeAnalysisServices(
        controls=[control(), control()],
        analysis_error=ModelPermissionError("sensitive provider message"),
    )

    result = await run_workflow(services)

    assert result["failureCode"] == "analysis_failed_modelpermissionerror_http_403_forbidden"
