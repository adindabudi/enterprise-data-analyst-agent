from __future__ import annotations

from typing import Literal

import pytest
from eda_worker.acceptance.documents import DOCUMENT_KINDS
from eda_worker.sandbox.benchmark import BenchmarkFailure, SessionMetric, evaluate_benchmark


def metric(kind: Literal["core", "document"], index: int) -> SessionMetric:
    return SessionMetric(
        kind=kind,
        ordinal=index,
        completed=True,
        allocation_ms=100,
        first_health_ms=100,
        import_ms=50,
        execution_ms=500,
        render_ms=200,
        validation_ms=50,
        stop_ms=1000,
        peak_rss_bytes=512 * 1024 * 1024,
        cpu_time_ms=250,
        output_bytes=4096,
        output_files=4,
        oom=False,
        process_escape=False,
        file_escape=False,
        network_success=False,
        cross_session_leakage=False,
        lingering_after_stop=False,
    )


def valid_metrics() -> list[SessionMetric]:
    return [
        *(metric("core", index) for index in range(10)),
        *(metric("document", index) for index in range(len(DOCUMENT_KINDS))),
    ]


def test_benchmark_requires_exact_corpus_and_computes_stop_p95() -> None:
    result = evaluate_benchmark(valid_metrics())

    assert result.status == "passed"
    assert result.core_sessions == 10
    assert result.document_sessions == len(DOCUMENT_KINDS)
    assert result.stop_p95_ms == 1000
    assert result.max_peak_rss_bytes == 512 * 1024 * 1024


def test_core_only_benchmark_rejects_an_unrequested_document_wave() -> None:
    core = [metric("core", index) for index in range(10)]

    result = evaluate_benchmark(core, require_documents=False)
    assert result.document_sessions == 0
    with pytest.raises(BenchmarkFailure):
        evaluate_benchmark([*core, metric("document", 0)], require_documents=False)


@pytest.mark.parametrize(
    "failed_metric",
    [
        metric("core", 0).model_copy(update={"peak_rss_bytes": 3_435_973_837}),
        metric("core", 0).model_copy(update={"cross_session_leakage": True}),
        metric("core", 0).model_copy(update={"network_success": True}),
        metric("core", 0).model_copy(update={"lingering_after_stop": True}),
        metric("core", 0).model_copy(update={"stop_ms": 16_000}),
    ],
)
def test_benchmark_rejects_resource_or_isolation_failure(failed_metric: SessionMetric) -> None:
    metrics = valid_metrics()
    metrics[0] = failed_metric

    with pytest.raises(BenchmarkFailure):
        evaluate_benchmark(metrics)
