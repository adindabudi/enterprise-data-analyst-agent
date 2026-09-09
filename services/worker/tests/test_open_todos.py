from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from eda_contracts.tasks import TaskStatus
from eda_runtime_state.models import TaskRecord
from eda_worker.finalization import unfinished_note
from eda_worker.history.models import SessionPartition, TodoProjection
from eda_worker.history.todos import SessionOpenTodos

TENANT = UUID("6174cc31-519e-4b5a-9a6b-2a2c700a4447")
OWNER = UUID("375495d5-8017-4fe7-9362-29d0a468c8fb")
SESSION = "ses_0lQXRtv1YKmFPwDN1iW5zyZ2B11haarA"

# Verbatim shape of what the framework stored for the run that finished with nothing.
STORED = (
    {"id": 1, "title": "Menyiapkan rencana ekspor Excel", "description": "…", "is_complete": False},
    {"id": 2, "title": "Membuat dan memvalidasi workbook", "description": "…", "is_complete": False},
)


class Source:
    def __init__(self, items: tuple[dict[str, object], ...] | None) -> None:
        self._items = items

    async def load_todos(self, partition: SessionPartition) -> TodoProjection | None:
        assert partition.session_id == SESSION
        if self._items is None:
            return None
        return TodoProjection(
            tenant_id=TENANT,
            owner_object_id=OWNER,
            session_id=SESSION,
            items=self._items,
            updated_at=datetime.now(UTC),
        )


def task() -> TaskRecord:
    now = datetime.now(UTC)
    return TaskRecord(
        id="task_fV_zBf3kbpn1R5uSoBKPDbdaYqntBxKx",
        tenant_id=TENANT,
        owner_object_id=OWNER,
        session_id=SESSION,
        status=TaskStatus.COMPLETED,
        checkpoint_sequence=0,
        command_sequence=0,
        applied_command_sequence=0,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(days=1),
    )


@pytest.mark.asyncio
async def test_a_plan_the_agent_never_finished_is_reported() -> None:
    open_todos = await SessionOpenTodos(Source(STORED)).open_todos(task())

    assert open_todos == ("Menyiapkan rencana ekspor Excel", "Membuat dan memvalidasi workbook")


@pytest.mark.asyncio
async def test_a_finished_plan_reports_nothing() -> None:
    done = tuple({**item, "is_complete": True} for item in STORED)

    assert await SessionOpenTodos(Source(done)).open_todos(task()) == ()


@pytest.mark.asyncio
async def test_a_run_that_wrote_no_plan_reports_nothing() -> None:
    assert await SessionOpenTodos(Source(None)).open_todos(task()) == ()


def test_the_note_names_what_was_left_open() -> None:
    note = unfinished_note(("Membuat dan memvalidasi workbook",))

    # Without this the user reads "Analysis completed" and waits for a file that never comes.
    assert "stopped before finishing its own plan" in note
    assert "Membuat dan memvalidasi workbook" in note


def test_a_finished_run_gets_no_note() -> None:
    assert unfinished_note(()) == ""
