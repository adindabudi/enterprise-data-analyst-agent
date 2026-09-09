from __future__ import annotations

import pytest

from .evidence import DocumentAcceptanceObservations

pytestmark = pytest.mark.cloud


def test_measured_three_session_document_benchmark_passes_release_limits(
    document_observations: DocumentAcceptanceObservations,
) -> None:
    assert document_observations.document_sessions == 4
    assert document_observations.max_peak_memory_ratio < 0.8
    assert document_observations.max_duration_seconds <= 300
    assert document_observations.oom_count == 0
    assert document_observations.cross_session_leak_count == 0
    assert len(document_observations.benchmark_sha256) == 64
    assert document_observations.tests["concurrency"] == "passed"
