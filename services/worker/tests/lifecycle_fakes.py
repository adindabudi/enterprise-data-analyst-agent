"""Fakes for driving the analysis lifecycle without a model, storage or sandbox."""

from __future__ import annotations

from collections import deque
from datetime import UTC, datetime, timedelta
from uuid import UUID

from eda_contracts.tasks import TaskStatus
from eda_runtime_state.models import TaskRecord
from eda_worker.contracts import ControlSnapshot


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
