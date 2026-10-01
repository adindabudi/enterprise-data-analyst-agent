from __future__ import annotations

import logging
import os
import sys
from collections.abc import Callable, Mapping, MutableMapping
from typing import Any, cast

logger = logging.getLogger(__name__)

REDACTED_ATTRIBUTE_KEYS = frozenset(
    {
        "http.request.body",
        "http.response.body",
        "gen_ai.prompt",
        "gen_ai.completion",
        "gen_ai.input.messages",
        "gen_ai.output.messages",
        "gen_ai.system_instructions",
        "gen_ai.tool.call.arguments",
        "gen_ai.tool.call.result",
        "db.statement",
        "db.query.text",
        "authorization",
        "cookie",
        "tool.arguments",
        "tool.result",
    }
)
# Indexed forms such as gen_ai.prompt.0.content are the same content under another name.
REDACTED_ATTRIBUTE_PREFIXES = ("enduser.", "file.", "gen_ai.prompt.", "gen_ai.completion.")

_redaction_failure_reported = False


def redact_attributes(attributes: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in attributes.items() if not _is_redacted_attribute(key)}


def _is_redacted_attribute(key: str) -> bool:
    normalized = key.lower()
    return (
        normalized in REDACTED_ATTRIBUTE_KEYS
        or normalized.startswith(REDACTED_ATTRIBUTE_PREFIXES)
        or normalized.endswith(".authorization")
        or normalized.endswith(".cookie")
    )


def _report_redaction_failure(surface: str) -> None:
    global _redaction_failure_reported
    if _redaction_failure_reported:
        return
    _redaction_failure_reported = True
    logger.error("telemetry redaction could not run on %s; content may reach the exporter", surface)


def _redact_log_record(record: object) -> None:
    attributes = getattr(record, "attributes", None)
    if attributes is None:
        log_record = getattr(record, "log_record", None)
        if log_record is not None:
            _redact_log_record(log_record)
        return
    if not isinstance(attributes, MutableMapping):
        _report_redaction_failure("log records")
        return
    typed_attributes = cast("MutableMapping[str, Any]", attributes)
    for key in tuple(typed_attributes):
        if _is_redacted_attribute(key):
            del typed_attributes[key]


def _redact_span(span: Any) -> None:
    """Replaces the mappings instead of mutating them: they are sealed before the processor runs."""
    attributes = getattr(span, "_attributes", None)
    if isinstance(attributes, Mapping):
        span._attributes = redact_attributes(cast("Mapping[str, Any]", attributes))
    for event in getattr(span, "_events", ()) or ():
        event_attributes = getattr(event, "_attributes", None)
        if isinstance(event_attributes, Mapping):
            event._attributes = redact_attributes(cast("Mapping[str, Any]", event_attributes))


def _content_survived(span: object) -> bool:
    mappings: list[Mapping[str, Any]] = [cast("Mapping[str, Any]", getattr(span, "attributes", None) or {})]
    events = cast("tuple[object, ...]", getattr(span, "events", ()) or ())
    mappings.extend(cast("Mapping[str, Any]", getattr(event, "attributes", None) or {}) for event in events)
    return any(_is_redacted_attribute(key) for mapping in mappings for key in mapping)


class ContentFreeSpanProcessor:
    """Redacts in `_on_ending`, where the span still owns its mappings.

    By `on_end` the SDK has wrapped them in a read-only view, so the earlier in-place version
    of this processor silently exported everything it was written to drop.
    """

    def on_start(self, span: object, parent_context: object | None = None) -> None:
        del span, parent_context

    def _on_ending(self, span: object) -> None:
        _redact_span(span)

    def on_end(self, span: object) -> None:
        # A tripwire, because the failure this replaces was invisible for as long as it existed.
        if _content_survived(span):
            _report_redaction_failure("spans")

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        del timeout_millis
        return True


class ContentFreeLogRecordProcessor:
    def on_emit(self, log_record: object) -> None:
        _redact_log_record(log_record)

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        del timeout_millis
        return True


def configure_logging() -> None:
    """Uvicorn only configures its own loggers, so ours reached stderr unformatted or not at all.

    Scoped to our own loggers: at INFO the Azure SDKs narrate every HTTP call. The analyst runtime
    runs in this process, so its `eda_worker` loggers print here too; their level is left to the
    process, so warnings and failures show without the worker's INFO narration.
    """
    for name, level in (("eda_api", logging.INFO), ("eda_worker", None)):
        logger = logging.getLogger(name)
        if logger.handlers:
            continue
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s: %(message)s"))
        logger.addHandler(handler)
        if level is not None:
            logger.setLevel(level)


def configure_telemetry() -> None:
    connection_string = os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING")
    if not connection_string:
        return
    from azure.monitor.opentelemetry import configure_azure_monitor  # type: ignore[reportUnknownVariableType]
    from opentelemetry.sdk.resources import Resource

    configure = cast("Callable[..., None]", configure_azure_monitor)
    # Sampling is left to OTEL_TRACES_SAMPLER_ARG. A hard-coded ratio dropped nine of ten traces
    # at pilot volume, which is where a single reported failure has to be findable.
    configure(
        connection_string=connection_string,
        resource=Resource.create({"service.name": "eda-api"}),
        span_processors=[ContentFreeSpanProcessor()],
        log_record_processors=[ContentFreeLogRecordProcessor()],
    )
