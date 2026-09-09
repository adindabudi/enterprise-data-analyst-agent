"""Instruments named in docs/operations/slo.md.

The names are load-bearing: the SLO queries read `customMetrics` by name. Attributes stay low
cardinality deliberately -- a stream that exceeds the SDK cardinality limit is folded into a
single overflow point that carries none of the original attributes.
"""

from __future__ import annotations

from opentelemetry import metrics

PROGRESS_FIRST = "eda.progress.first"
MODEL_FIRST = "eda.model.first"

_meter = metrics.get_meter("eda.api")
_progress_first = _meter.create_histogram(
    PROGRESS_FIRST,
    unit="ms",
    description="Delay before a turn shows the caller any movement.",
)
_model_first = _meter.create_histogram(
    MODEL_FIRST,
    unit="ms",
    description="Delay before the model produces its first visible content.",
)


def record_first_progress(milliseconds: float) -> None:
    _progress_first.record(max(milliseconds, 0.0))


def record_first_model_content(milliseconds: float, *, model: str) -> None:
    _model_first.record(max(milliseconds, 0.0), {"model": model})
