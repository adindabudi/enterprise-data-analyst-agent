from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from eda_contracts.controls import CommandKind
from eda_contracts.tasks import TaskStatus
from eda_runtime_state.models import (
    InvalidTransition,
    OperationKeyInput,
    TaskPartition,
    TaskRecord,
    canonical_operation_key,
    transition,
)

PARTITION = TaskPartition(
    tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
    owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
    session_id="ses_01HZZZZZZZZZZZZZZZZZZZZZZZ",
)


@pytest.mark.parametrize(
    ("current", "next_status"),
    [
        (TaskStatus.PLANNING, TaskStatus.ACQUIRING_DATA),
        (TaskStatus.ACQUIRING_DATA, TaskStatus.ANALYZING),
        (TaskStatus.ANALYZING, TaskStatus.GENERATING),
        (TaskStatus.GENERATING, TaskStatus.VALIDATING),
        (TaskStatus.VALIDATING, TaskStatus.PUBLISHING),
        (TaskStatus.PUBLISHING, TaskStatus.COMPLETED),
    ],
)
def test_happy_path_transitions(current: TaskStatus, next_status: TaskStatus) -> None:
    assert transition(current, next_status) == next_status


def test_completed_task_cannot_transition() -> None:
    with pytest.raises(InvalidTransition):
        transition(TaskStatus.COMPLETED, TaskStatus.ANALYZING)


def test_operation_key_is_order_independent_and_input_sensitive() -> None:
    left = canonical_operation_key(
        OperationKeyInput(
            workflow_instance_id="task_12345678",
            step_name="sandbox-execute",
            canonical_input={"b": 2, "a": 1},
        )
    )
    right = canonical_operation_key(
        OperationKeyInput(
            workflow_instance_id="task_12345678",
            step_name="sandbox-execute",
            canonical_input={"a": 1, "b": 2},
        )
    )
    changed = canonical_operation_key(
        OperationKeyInput(
            workflow_instance_id="task_12345678",
            step_name="sandbox-execute",
            canonical_input={"a": 2, "b": 2},
        )
    )

    assert left == right
    assert left != changed


def test_command_kinds_are_narrow() -> None:
    assert {kind.value for kind in CommandKind} == {"steer", "cancel", "auth_resumed"}


def test_task_record_serializes_workspace_hpk_and_fields_as_camel_case() -> None:
    now = datetime.now(UTC)
    record = TaskRecord(
        id="task_12345678",
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
        session_id="ses_1234567890abcdef",
        status=TaskStatus.PLANNING,
        checkpoint_sequence=0,
        command_sequence=0,
        applied_command_sequence=0,
        sourceMessageId="msg_12345678",
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(hours=1),
    )

    body = record.model_dump(mode="json", by_alias=True)
    assert body["sourceMessageId"] == "msg_12345678"
    assert body["tenantId"] == str(record.tenant_id)
    assert body["ownerObjectId"] == str(record.owner_object_id)
    assert body["sessionId"] == record.session_id
    assert body["recordType"] == "task"
    assert not ({"tenant_id", "owner_object_id", "session_id", "record_type", "source_message_id"} & body.keys())
