from __future__ import annotations

from datetime import UTC, datetime

import pytest
from eda_contracts import (
    ActivityEvent,
    ApiProblem,
    EventType,
    MessageDeltaEvent,
    MessageDeltaPayload,
    SessionSummary,
    TaskStatus,
)
from eda_contracts.events import AuthRequiredPayload
from pydantic import TypeAdapter, ValidationError


def test_every_declared_event_type_is_covered_by_union_schema() -> None:
    schema_text = str(TypeAdapter(ActivityEvent).json_schema())
    for event_type in EventType:
        assert event_type.value in schema_text


def test_event_serializes_public_camel_case_shape() -> None:
    event = MessageDeltaEvent(
        event_id="evt_01HZZZZZZZZZZZZZZZZZZZZZZZ",
        sequence=142,
        response_attempt_id="attempt-3",
        attempt_sequence=17,
        session_id="ses_01HZZZZZZZZZZZZZZZZZZZZZZZ",
        task_id="task_01HZZZZZZZZZZZZZZZZZZZZZZZ",
        occurred_at=datetime(2026, 7, 23, 10, 15, 30, tzinfo=UTC),
        payload=MessageDeltaPayload(delta="Revenue increased"),
    )

    body = event.model_dump(mode="json", by_alias=True)
    assert body["eventId"].startswith("evt_")
    assert body["responseAttemptId"] == "attempt-3"
    assert body["payload"] == {"delta": "Revenue increased"}
    assert "event_id" not in body


@pytest.mark.parametrize("forbidden", ["accessToken", "refreshToken", "authorization", "cookie", "apiKey"])
def test_event_payload_rejects_credential_shaped_fields(forbidden: str) -> None:
    with pytest.raises(ValidationError):
        MessageDeltaPayload.model_validate({"delta": "safe", forbidden: "secret"})


def test_auth_required_action_is_relative_and_provider_free() -> None:
    payload = AuthRequiredPayload()

    assert payload.model_dump(mode="json", by_alias=True) == {"actionPath": "/api/fabric/auth/start"}
    with pytest.raises(ValidationError):
        AuthRequiredPayload.model_validate(
            {"actionPath": "https://login.microsoftonline.com/tenant/oauth2/v2.0/authorize"}
        )
    with pytest.raises(ValidationError):
        AuthRequiredPayload.model_validate({"actionPath": "/api/fabric/auth/start", "provider": "fabric_iq"})


def test_task_terminal_statuses_are_explicit() -> None:
    terminal_statuses = {
        TaskStatus.COMPLETED,
        TaskStatus.CANCELLED,
        TaskStatus.FAILED,
        TaskStatus.FAILED_CANCELLATION,
    }
    non_terminal_statuses = set(TaskStatus) - terminal_statuses
    assert non_terminal_statuses == {
        TaskStatus.PLANNING,
        TaskStatus.ACQUIRING_DATA,
        TaskStatus.ANALYZING,
        TaskStatus.GENERATING,
        TaskStatus.VALIDATING,
        TaskStatus.PUBLISHING,
        TaskStatus.BLOCKED_AUTH,
        TaskStatus.CANCELLING,
    }


def test_api_problem_is_bounded_and_filters_unknown_fields() -> None:
    problem = ApiProblem(status=409, title="Conflict", code="active_task_exists", correlation_id="corr_12345678")
    assert problem.model_dump(mode="json", by_alias=True) == {
        "type": "about:blank",
        "title": "Conflict",
        "status": 409,
        "code": "active_task_exists",
        "correlationId": "corr_12345678",
        "detail": None,
    }
    with pytest.raises(ValidationError):
        ApiProblem.model_validate({**problem.model_dump(), "exception": "internal stack"})


def test_session_summary_serializes_only_public_session_fields() -> None:
    summary = SessionSummary(
        session_id="ses_01HZZZZZZZZZZZZZZZZZZZZZZZ",
        title="Variance review",
        last_activity_at=datetime(2026, 7, 24, 10, 15, 30, tzinfo=UTC),
    )

    assert summary.model_dump(mode="json", by_alias=True) == {
        "sessionId": "ses_01HZZZZZZZZZZZZZZZZZZZZZZZ",
        "title": "Variance review",
        "lastActivityAt": "2026-07-24T10:15:30Z",
    }
