from __future__ import annotations

from eda_worker.telemetry import REDACTED_ATTRIBUTE_KEYS, redact_attributes


def test_worker_telemetry_drops_content_and_preserves_operational_attributes() -> None:
    attributes = {
        "http.response.body": "confidential result",
        "tool.arguments": "SELECT * FROM customer",
        "cookie": "session=sentinel-secret",
        "task.id": "task_12345678",
        "artifact.kind": "workbook",
        "retry.count": 2,
    }

    redacted = redact_attributes(attributes)

    assert {"http.response.body", "tool.arguments", "cookie"}.issubset(REDACTED_ATTRIBUTE_KEYS)
    assert "confidential result" not in str(redacted)
    assert "sentinel-secret" not in str(redacted)
    assert redacted == {
        "task.id": "task_12345678",
        "artifact.kind": "workbook",
        "retry.count": 2,
    }
