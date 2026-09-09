"""Instruments named in docs/operations/slo.md and infra/bicep/modules/budgets-alerts.bicep.

The names are load-bearing: an alert queries `customMetrics` by name, so renaming one here
silently disarms it. Attributes stay low cardinality deliberately. A stream that exceeds the
SDK cardinality limit is folded into a single overflow point that carries none of the original
attributes, so a task ID here would take every breakdown down with it.
"""

from __future__ import annotations

from functools import cache

from opentelemetry import metrics

TASK_COMPLETED = "eda.task.completed"
TASK_FAILED = "eda.task.failed"
CANCEL_ACK = "eda.cancel.ack"
PUBLISH_STRUCTURAL = "eda.publish.structural"
SESSION_STOP = "eda.session.stop"


@cache
def _instruments():  # type: ignore[no-untyped-def]
    meter = metrics.get_meter("eda.worker")
    return (
        meter.create_counter(TASK_COMPLETED, description="Analysis runs that reached a final message."),
        meter.create_counter(TASK_FAILED, description="Analysis runs that ended in a failure status."),
        meter.create_histogram(
            CANCEL_ACK,
            unit="ms",
            description="Delay between a cancellation request and the run acting on it.",
        ),
        meter.create_histogram(
            PUBLISH_STRUCTURAL,
            description="1 when publication was preceded by a passing structural validation, 0 otherwise.",
        ),
        meter.create_histogram(
            SESSION_STOP,
            unit="ms",
            description="Time taken to delete a task sandbox.",
        ),
    )


def record_task_completed() -> None:
    _instruments()[0].add(1)


def record_task_failed() -> None:
    _instruments()[1].add(1)


def record_cancel_ack(milliseconds: float) -> None:
    _instruments()[2].record(max(milliseconds, 0.0))


def record_publish_structural(*, passed: bool) -> None:
    _instruments()[3].record(1 if passed else 0)


def record_session_stop(milliseconds: float, *, outcome: str) -> None:
    _instruments()[4].record(max(milliseconds, 0.0), {"outcome": outcome})
