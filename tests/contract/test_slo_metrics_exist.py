from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SLO = ROOT / "docs" / "operations" / "slo.md"
ALERTS = ROOT / "infra" / "bicep" / "modules" / "budgets-alerts.bicep"
METRIC_MODULES = (
    ROOT / "apps" / "api" / "src" / "eda_api" / "metrics.py",
    ROOT / "services" / "worker" / "src" / "eda_worker" / "metrics.py",
)
METRIC_NAME = re.compile(r"eda\.[a-z]+\.[a-z]+")


def _emitted_names() -> set[str]:
    return {name for module in METRIC_MODULES for name in METRIC_NAME.findall(module.read_text(encoding="utf-8"))}


def test_every_objective_reads_a_metric_something_emits() -> None:
    # The SLO table and two deployed alerts once named metrics no code produced, so the alerts
    # could not fire and no objective was measurable. Only a name check catches that again.
    assert set(METRIC_NAME.findall(SLO.read_text(encoding="utf-8"))) <= _emitted_names()


def test_every_alert_reads_a_metric_something_emits() -> None:
    assert set(METRIC_NAME.findall(ALERTS.read_text(encoding="utf-8"))) <= _emitted_names()


def test_no_alert_groups_a_metric_by_an_unbounded_attribute() -> None:
    # A stream past the SDK cardinality limit is folded into one overflow point that carries none
    # of the original attributes, so a per-task breakdown silently stops returning rows.
    alerts = ALERTS.read_text(encoding="utf-8")
    for line in alerts.splitlines():
        if "customMetrics" not in line:
            continue
        assert "taskId" not in line
        assert "sessionId" not in line
