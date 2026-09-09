from __future__ import annotations

import pytest
from opentelemetry import metrics
from opentelemetry.sdk.metrics import Counter, Histogram, MeterProvider
from opentelemetry.sdk.metrics.export import AggregationTemporality, InMemoryMetricReader

_TEST_METER_READER: InMemoryMetricReader | None = None


def pytest_configure() -> None:
    global _TEST_METER_READER
    if _TEST_METER_READER is not None:
        return
    _TEST_METER_READER = InMemoryMetricReader(
        preferred_temporality={Counter: AggregationTemporality.DELTA, Histogram: AggregationTemporality.DELTA}
    )
    metrics.set_meter_provider(MeterProvider(metric_readers=[_TEST_METER_READER]))


@pytest.fixture(scope="session")
def _meter_reader() -> InMemoryMetricReader:
    """A process accepts one MeterProvider, so API and worker tests have to share this one."""
    if _TEST_METER_READER is None:
        raise RuntimeError("test MeterProvider was not configured before collection")
    return _TEST_METER_READER


@pytest.fixture
def meter_reader(_meter_reader: InMemoryMetricReader) -> InMemoryMetricReader:
    _meter_reader.get_metrics_data()
    return _meter_reader


@pytest.fixture
def metric_points(meter_reader: InMemoryMetricReader):  # type: ignore[no-untyped-def]
    # Delta collection drains the reader, so the snapshot is taken once and then read from.
    snapshot: dict[str, list[tuple[float, dict[str, object]]]] = {}
    collected = False

    def points(name: str) -> list[tuple[float, dict[str, object]]]:
        nonlocal collected
        if not collected:
            collected = True
            data = meter_reader.get_metrics_data()
            for resource in data.resource_metrics if data is not None else []:
                for scope in resource.scope_metrics:
                    for metric in scope.metrics:
                        snapshot[metric.name] = [
                            (
                                float(getattr(point, "value", None) or getattr(point, "sum", 0)),
                                dict(point.attributes or {}),
                            )
                            for point in metric.data.data_points
                        ]
        return snapshot.get(name, [])

    return points
