from __future__ import annotations

import logging
from typing import Any, ClassVar, cast

import pytest
from eda_worker.telemetry import (
    REDACTED_ATTRIBUTE_KEYS,
    ContentFreeLogRecordProcessor,
    ContentFreeSpanProcessor,
    configure_logging,
    redact_attributes,
)


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


def test_worker_processors_strip_sentinel_content_before_export(monkeypatch: pytest.MonkeyPatch) -> None:
    # Against a hand-rolled stand-in this passed while the real span path exported everything:
    # the SDK hands `on_end` a read-only view, so the in-place version silently did nothing.
    monkeypatch.setattr("eda_worker.telemetry._redaction_failure_reported", False)
    spans, logs = _export_through_real_sdk(
        attributes={
            "tool.arguments": "SENTINEL_ARGUMENTS",
            "gen_ai.prompt.0.content": "SENTINEL_NESTED",
            "task.id": "task_opaque",
        },
        event=("gen_ai.user.message", {"gen_ai.prompt": "SENTINEL_EVENT", "step": 1}),
        log_attributes={"authorization": "Bearer SENTINEL_AUTHORIZATION", "operation.id": "operation_opaque"},
    )

    assert "SENTINEL" not in str(spans)
    assert "SENTINEL" not in str(logs)
    assert spans[0][0] == {"task.id": "task_opaque"}
    assert spans[0][1] == [{"step": 1}]
    assert logs[0]["operation.id"] == "operation_opaque"


def test_worker_reports_when_redaction_cannot_run(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr("eda_worker.telemetry._redaction_failure_reported", False)

    class Sealed:
        attributes: ClassVar[dict[str, str]] = {"tool.arguments": "SENTINEL_ARGUMENTS"}
        events = ()

    with caplog.at_level(logging.ERROR):
        ContentFreeSpanProcessor().on_end(Sealed())

    assert "redaction could not run" in caplog.text


def _export_through_real_sdk(
    *,
    attributes: dict[str, object],
    event: tuple[str, dict[str, object]],
    log_attributes: dict[str, object],
) -> tuple[list[tuple[dict[str, object], list[dict[str, object]]]], list[dict[str, object]]]:
    from opentelemetry.instrumentation.logging.handler import LoggingHandler
    from opentelemetry.sdk._logs import LoggerProvider
    from opentelemetry.sdk._logs.export import SimpleLogRecordProcessor
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor

    spans: list[tuple[dict[str, object], list[dict[str, object]]]] = []
    logs: list[dict[str, object]] = []

    class CaptureSpans:
        def export(self, batch: Any) -> Any:
            spans.extend((dict(s.attributes or {}), [dict(e.attributes or {}) for e in s.events]) for s in batch)
            return None

        def shutdown(self) -> None: ...

        def force_flush(self, timeout_millis: int = 30000) -> bool:
            return True

    class CaptureLogs:
        def export(self, batch: Any) -> Any:
            logs.extend(dict(entry.log_record.attributes or {}) for entry in batch)
            return None

        def shutdown(self) -> None: ...

    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(ContentFreeSpanProcessor())
    tracer_provider.add_span_processor(SimpleSpanProcessor(cast(Any, CaptureSpans())))
    with tracer_provider.get_tracer("test").start_as_current_span("run") as span:
        for key, value in attributes.items():
            span.set_attribute(key, cast(Any, value))
        span.add_event(event[0], cast(Any, event[1]))

    logger_provider = LoggerProvider()
    logger_provider.add_log_record_processor(ContentFreeLogRecordProcessor())
    logger_provider.add_log_record_processor(SimpleLogRecordProcessor(cast(Any, CaptureLogs())))
    emitter = logging.getLogger("eda_worker.telemetry.test")
    emitter.propagate = False
    handler = LoggingHandler(logger_provider=logger_provider)
    emitter.addHandler(handler)
    try:
        emitter.error("probe", extra=log_attributes)
    finally:
        emitter.removeHandler(handler)
    return spans, logs


def test_worker_telemetry_configures_service_resource_and_content_free_processors(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from eda_worker.telemetry import ContentFreeLogRecordProcessor, ContentFreeSpanProcessor, configure_telemetry

    configured: dict[str, object] = {}
    monkeypatch.setenv("APPLICATIONINSIGHTS_CONNECTION_STRING", "InstrumentationKey=sentinel")
    monkeypatch.setattr(
        "azure.monitor.opentelemetry.configure_azure_monitor",
        lambda **kwargs: configured.update(kwargs),
    )

    configure_telemetry()

    resource = configured["resource"]
    assert resource.attributes["service.name"] == "eda-worker"  # type: ignore[union-attr]
    assert isinstance(configured["span_processors"][0], ContentFreeSpanProcessor)  # type: ignore[index]
    assert isinstance(configured["log_record_processors"][0], ContentFreeLogRecordProcessor)  # type: ignore[index]


def test_our_log_records_reach_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    logger = logging.getLogger("eda_worker")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
    try:
        configure_logging()
        logging.getLogger("eda_worker.agent.progress").info("tool call name=%s", "publish_artifact")
        # Without a handler the record is dropped, which is how a whole run left no trace.
        assert "tool call name=publish_artifact" in capsys.readouterr().out
    finally:
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
        logger.setLevel(logging.NOTSET)
