from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..acceptance.documents import DOCUMENT_KINDS

MEMORY_LIMIT_BYTES = 3_435_973_836
STOP_P95_LIMIT_MS = 15_000


class BenchmarkFailure(RuntimeError):
    pass


class BenchmarkModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SessionMetric(BenchmarkModel):
    kind: Literal["core", "document"]
    ordinal: int = Field(ge=0)
    completed: bool
    allocation_ms: int = Field(ge=0)
    first_health_ms: int = Field(ge=0)
    import_ms: int = Field(ge=0)
    execution_ms: int = Field(ge=0)
    render_ms: int = Field(ge=0)
    validation_ms: int = Field(ge=0)
    stop_ms: int = Field(ge=0)
    peak_rss_bytes: int = Field(ge=0)
    cpu_time_ms: int = Field(ge=0)
    output_bytes: int = Field(ge=0)
    output_files: int = Field(ge=0)
    oom: bool
    process_escape: bool
    file_escape: bool
    network_success: bool
    cross_session_leakage: bool
    lingering_after_stop: bool
    failure_stage: str | None = None
    failure_code: str | None = None


class BenchmarkSummary(BenchmarkModel):
    status: Literal["passed"] = "passed"
    core_sessions: int
    document_sessions: int
    stop_p95_ms: int
    max_peak_rss_bytes: int
    total_output_bytes: int
    total_output_files: int


def evaluate_benchmark(metrics: Sequence[SessionMetric], *, require_documents: bool = True) -> BenchmarkSummary:
    core = [metric for metric in metrics if metric.kind == "core"]
    documents = [metric for metric in metrics if metric.kind == "document"]
    expected_documents = len(DOCUMENT_KINDS) if require_documents else 0
    if len(core) != 10 or len(documents) != expected_documents:
        raise BenchmarkFailure(f"benchmark requires exactly ten Core and {expected_documents} document sessions")
    if not all(metric.completed for metric in metrics):
        raise BenchmarkFailure("one or more benchmark sessions did not complete")
    max_peak_rss = max(metric.peak_rss_bytes for metric in metrics)
    if max_peak_rss >= MEMORY_LIMIT_BYTES:
        raise BenchmarkFailure("one or more sessions exceeded 80% of the 4 GiB memory profile")
    isolation_failures = any(
        metric.oom
        or metric.process_escape
        or metric.file_escape
        or metric.network_success
        or metric.cross_session_leakage
        or metric.lingering_after_stop
        for metric in metrics
    )
    if isolation_failures:
        raise BenchmarkFailure("one or more sessions failed an isolation assertion")
    stop_p95 = _nearest_rank_percentile([metric.stop_ms for metric in metrics], 0.95)
    if stop_p95 > STOP_P95_LIMIT_MS:
        raise BenchmarkFailure("session stop P95 exceeded 15 seconds")
    return BenchmarkSummary(
        core_sessions=len(core),
        document_sessions=len(documents),
        stop_p95_ms=stop_p95,
        max_peak_rss_bytes=max_peak_rss,
        total_output_bytes=sum(metric.output_bytes for metric in metrics),
        total_output_files=sum(metric.output_files for metric in metrics),
    )


def _nearest_rank_percentile(values: Sequence[int], percentile: float) -> int:
    if not values:
        raise BenchmarkFailure("percentile input is empty")
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[rank - 1]
