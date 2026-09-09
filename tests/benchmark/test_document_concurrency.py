from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from eda_worker.acceptance.documents import DOCUMENT_KINDS
from eda_worker.sandbox.benchmark import MEMORY_LIMIT_BYTES, SessionMetric


def benchmark_results() -> list[SessionMetric]:
    path_value = os.getenv("EDA_SANDBOX_BENCHMARK_RESULTS")
    if not path_value:
        pytest.skip("EDA_SANDBOX_BENCHMARK_RESULTS is required for measured document concurrency")
    payload: object = json.loads(Path(path_value).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("sessions"), list):
        raise ValueError("sandbox benchmark result artifact is malformed")
    return [SessionMetric.model_validate(value) for value in payload["sessions"]]


def test_one_concurrent_document_task_per_kind_meets_the_release_gate() -> None:
    document_runs = [run for run in benchmark_results() if run.kind == "document"]

    # The benchmark runs one session per kind, so this count moves with the skill set.
    assert len(document_runs) == len(DOCUMENT_KINDS)
    assert all(run.completed for run in document_runs)
    assert all(run.peak_rss_bytes < MEMORY_LIMIT_BYTES for run in document_runs)
    assert all(run.execution_ms <= 300_000 for run in document_runs)
    assert all(not run.oom and not run.cross_session_leakage for run in document_runs)
    assert all(not run.process_escape and not run.file_escape and not run.network_success for run in document_runs)
