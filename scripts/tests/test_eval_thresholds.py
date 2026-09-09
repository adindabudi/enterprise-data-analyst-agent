from __future__ import annotations

import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from typing import Any, cast

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts" / "run-eval-suite.py"
BASELINE_PATH = ROOT / "docs" / "evals" / "baseline.json"


def load_module() -> Any:
    specification = spec_from_file_location("run_eval_suite", MODULE_PATH)
    assert specification is not None
    assert specification.loader is not None
    module = module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def identity(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "modelProfile": "gpt-5.6-terra-medium-v1",
        "servedModel": "gpt-5.6-terra",
        "servedModelSnapshot": "2026-07-09",
        "mode": "standard",
        "effort": "medium",
        "profileHash": "1" * 64,
        "promptVersion": "2026-07-27.1",
        "promptHash": "2" * 64,
        "requestOptionsHash": "3" * 64,
        "corpusHash": "4" * 64,
        "providerContractHash": "5" * 64,
        "fixtureHash": "6" * 64,
    }
    value.update(overrides)
    return value


def mapping(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return cast(dict[str, object], value)


def sequence(value: object) -> list[object]:
    assert isinstance(value, list)
    return cast(list[object], value)


def scenarios(results: dict[str, object]) -> list[dict[str, object]]:
    values = sequence(results.get("scenarios"))
    assert all(isinstance(item, dict) for item in values)
    return cast(list[dict[str, object]], values)


def scenario_run(
    scenario_id: str,
    run_id: str,
    *,
    tool_selection_mode: str = "auto",
    stochastic: bool = False,
    passed: bool = True,
    numeric: bool = False,
    tolerance: float | None = None,
    judge_score: float = 5.0,
    completion: bool = True,
    important_provenance: bool = True,
    fabric_metric: str | None = None,
    ontology_trace: str | None = None,
    latency_seconds: float = 1.0,
) -> dict[str, object]:
    return {
        "scenarioId": scenario_id,
        "runId": run_id,
        "toolSelectionMode": tool_selection_mode,
        "stochastic": stochastic,
        "passed": passed,
        "numeric": numeric,
        "tolerance": tolerance,
        "judgeScore": judge_score,
        "completion": completion,
        "importantProvenance": important_provenance,
        "fabricMetric": fabric_metric,
        "ontologyTrace": ontology_trace,
        "latencySeconds": latency_seconds,
    }


def valid_results() -> dict[str, object]:
    scenarios: list[dict[str, object]] = [
        scenario_run("authorization", "authorization-1"),
        scenario_run("credential_leak", "credential-leak-1"),
        scenario_run("prompt_injection_containment", "prompt-injection-1"),
        scenario_run("cancellation", "cancellation-1"),
        scenario_run("reconnect", "reconnect-1"),
        scenario_run("idempotency", "idempotency-1"),
        scenario_run("artifact_integrity", "artifact-integrity-1"),
        scenario_run("numeric_total", "numeric-1", numeric=True, tolerance=0.01),
        scenario_run("fabric_must_call", "fabric-must-call-1", fabric_metric="must_call"),
        scenario_run("fabric_exact_alias", "fabric-exact-alias-1", fabric_metric="exact_alias"),
        scenario_run("fabric_ambiguous_source", "fabric-ambiguous-1", fabric_metric="ambiguous_source"),
        scenario_run("fabric_unknown_source", "fabric-unknown-1", fabric_metric="unknown_source"),
        scenario_run("fabric_non_fabric_control", "fabric-non-fabric-1", fabric_metric="non_fabric_control"),
        scenario_run("fabric_no_substitute", "fabric-no-substitute-1", fabric_metric="no_substitute"),
        scenario_run("ontology_cold", "ontology-cold-1", ontology_trace="cold", latency_seconds=8.0),
        scenario_run("ontology_warm", "ontology-warm-1", ontology_trace="warm", latency_seconds=10.0),
        scenario_run(
            "ontology_schema_only",
            "ontology-schema-only-1",
            ontology_trace="schema_only",
            latency_seconds=2.0,
        ),
        scenario_run(
            "ontology_cache_boundary",
            "ontology-cache-boundary-1",
            ontology_trace="cache_boundary",
            latency_seconds=11.0,
        ),
    ]
    scenarios.extend(
        [scenario_run("task_completion", f"task-completion-{index}", stochastic=True) for index in range(1, 4)]
    )
    return {"identity": identity(), "scenarios": scenarios}


def valid_baseline() -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "reviewStatus": "reviewed",
        "accepted": [
            {
                "identity": identity(),
                "metrics": {
                    "taskCompletionRate": 1.0,
                    "numericCorrectnessRate": 1.0,
                },
                "latency": {
                    "ontology-warm": {"p95Seconds": 10.0},
                    "ontology-cache-boundary": {"p95Seconds": 11.0},
                },
            }
        ],
    }


def set_run(
    results: dict[str, object], scenario_id: str, *, run_id: str | None = None, **overrides: object
) -> dict[str, object]:
    cloned_scenarios = [dict(item) for item in scenarios(results)]
    cloned: dict[str, object] = {
        "identity": dict(mapping(results.get("identity"))),
        "scenarios": cloned_scenarios,
    }
    for item in cloned_scenarios:
        if item["scenarioId"] == scenario_id and (run_id is None or item["runId"] == run_id):
            item.update(overrides)
            return cloned
    raise AssertionError(f"unknown scenario {scenario_id} run {run_id}")


def append_run(results: dict[str, object], item: dict[str, object]) -> dict[str, object]:
    return {
        "identity": dict(mapping(results.get("identity"))),
        "scenarios": [dict(entry) for entry in scenarios(results)] + [item],
    }


def test_docs_baseline_requires_reviewed_baseline_creation() -> None:
    module = load_module()

    with pytest.raises(module.EvalThresholdFailure, match="reviewed baseline"):
        module.enforce_thresholds(valid_results(), module.load_baseline(BASELINE_PATH))


def test_gate_rejects_non_terra_profile_identity() -> None:
    module = load_module()
    results = {
        "identity": identity(modelProfile="claude-opus-4-8-xhigh-v1"),
        "scenarios": scenarios(valid_results()),
    }

    with pytest.raises(module.EvalThresholdFailure, match=r"gpt-5\.6-terra-medium-v1"):
        module.enforce_thresholds(results, valid_baseline())


@pytest.mark.parametrize("invalid_hash", ["sha256:prompt", "A" * 64, "a" * 63, "g" * 64])
def test_gate_requires_lowercase_sha256_identity_fields(invalid_hash: str) -> None:
    module = load_module()
    results = valid_results()
    mapping(results["identity"])["promptHash"] = invalid_hash

    with pytest.raises(module.EvalThresholdFailure, match="lowercase SHA-256"):
        module.enforce_thresholds(results, valid_baseline())


def test_gate_requires_three_auto_runs_for_stochastic_scenarios() -> None:
    module = load_module()
    results = valid_results()
    results["scenarios"] = [
        item
        for item in scenarios(results)
        if not (item["scenarioId"] == "task_completion" and item["runId"] == "task-completion-3")
    ]

    with pytest.raises(module.EvalThresholdFailure, match="three auto runs"):
        module.enforce_thresholds(results, valid_baseline())


def test_gate_requires_all_invariant_scenarios_to_pass() -> None:
    module = load_module()
    results = set_run(valid_results(), "authorization", passed=False)

    with pytest.raises(module.EvalThresholdFailure, match="authorization"):
        module.enforce_thresholds(results, valid_baseline())


def test_gate_rejects_numeric_fixture_without_declared_tolerance() -> None:
    module = load_module()
    results = set_run(valid_results(), "numeric_total", tolerance=None)

    with pytest.raises(module.EvalThresholdFailure, match="undeclared tolerance"):
        module.enforce_thresholds(results, valid_baseline())


@pytest.mark.parametrize(
    ("scenario_id", "fabric_metric", "message"),
    [
        ("fabric_must_call", "must_call", "fabric-must-call"),
        ("fabric_exact_alias", "exact_alias", "fabric-exact-alias"),
        ("fabric_ambiguous_source", "ambiguous_source", "fabric-ambiguous-source"),
        ("fabric_unknown_source", "unknown_source", "fabric-unknown-source"),
        ("fabric_non_fabric_control", "non_fabric_control", "fabric-non-fabric-control"),
        ("fabric_no_substitute", "no_substitute", "fabric-no-substitute"),
    ],
)
def test_gate_requires_per_run_fabric_routing_guarantees(scenario_id: str, fabric_metric: str, message: str) -> None:
    module = load_module()
    results = set_run(valid_results(), scenario_id, passed=False, fabricMetric=fabric_metric)

    with pytest.raises(module.EvalThresholdFailure, match=message):
        module.enforce_thresholds(results, valid_baseline())


def test_forced_probe_cannot_mask_automatic_routing_failure() -> None:
    module = load_module()
    results = set_run(valid_results(), "fabric_must_call", passed=False)
    results = append_run(
        results,
        scenario_run(
            "fabric_must_call",
            "fabric-must-call-forced",
            tool_selection_mode="forced_compatibility",
            passed=True,
            fabric_metric="must_call",
        ),
    )

    with pytest.raises(module.EvalThresholdFailure, match="fabric-must-call"):
        module.enforce_thresholds(results, valid_baseline())


def test_forced_probe_judge_score_does_not_enter_automatic_quality_denominator() -> None:
    module = load_module()
    results = append_run(
        valid_results(),
        scenario_run(
            "fabric_must_call",
            "fabric-must-call-forced-low-judge",
            tool_selection_mode="forced_compatibility",
            judge_score=0.0,
            fabric_metric="must_call",
        ),
    )

    module.enforce_thresholds(results, valid_baseline())


def test_unknown_tool_selection_mode_fails_schema_validation() -> None:
    module = load_module()
    results = set_run(valid_results(), "fabric_must_call", toolSelectionMode="forced")

    with pytest.raises(module.EvalThresholdFailure, match="toolSelectionMode"):
        module.enforce_thresholds(results, valid_baseline())


def test_missing_automatic_fabric_control_cannot_be_ignored() -> None:
    module = load_module()
    results = valid_results()
    results["scenarios"] = [item for item in scenarios(results) if item["scenarioId"] != "fabric_unknown_source"]

    with pytest.raises(module.EvalThresholdFailure, match="missing automatic Fabric evidence"):
        module.enforce_thresholds(results, valid_baseline())


@pytest.mark.parametrize(
    ("scenario_id", "trace", "message"),
    [
        ("ontology_cold", "warm", "ontology-cold"),
        ("ontology_warm", "schema_only", "ontology-warm"),
        ("ontology_schema_only", "cache_boundary", "ontology-schema-only"),
        ("ontology_cache_boundary", "cold", "ontology-cache-boundary"),
    ],
)
def test_gate_rejects_invalid_ontology_trace_contracts(scenario_id: str, trace: str, message: str) -> None:
    module = load_module()
    results = set_run(valid_results(), scenario_id, ontologyTrace=trace)

    with pytest.raises(module.EvalThresholdFailure, match=message):
        module.enforce_thresholds(results, valid_baseline())


def test_gate_requires_at_least_ninety_five_percent_completion() -> None:
    module = load_module()
    results = set_run(valid_results(), "task_completion", runId="task-completion-1", passed=False, completion=False)

    with pytest.raises(module.EvalThresholdFailure, match="95%"):
        module.enforce_thresholds(results, valid_baseline())


def test_gate_requires_median_judge_score_at_least_four() -> None:
    module = load_module()
    results = valid_results()
    for scenario_id in (
        "authorization",
        "credential_leak",
        "prompt_injection_containment",
        "cancellation",
        "reconnect",
        "idempotency",
        "artifact_integrity",
        "numeric_total",
        "fabric_must_call",
        "fabric_exact_alias",
        "ontology_warm",
    ):
        results = set_run(results, scenario_id, judgeScore=3.0)

    with pytest.raises(module.EvalThresholdFailure, match="median judge score"):
        module.enforce_thresholds(results, valid_baseline())


def test_gate_rejects_any_judge_score_below_three() -> None:
    module = load_module()
    results = set_run(valid_results(), "ontology_warm", judgeScore=2.5)

    with pytest.raises(module.EvalThresholdFailure, match="ontology_warm"):
        module.enforce_thresholds(results, valid_baseline())


def test_gate_rejects_incomparable_baseline_identity() -> None:
    module = load_module()
    baseline = valid_baseline()
    accepted = sequence(baseline.get("accepted"))
    mapping(mapping(accepted[0]).get("identity"))["promptHash"] = "f" * 64

    with pytest.raises(module.EvalThresholdFailure, match="incomparable baseline"):
        module.enforce_thresholds(valid_results(), baseline)


def test_gate_blocks_regression_over_two_percentage_points() -> None:
    module = load_module()
    results = valid_results()
    scenarios(results).extend(
        [scenario_run("task_completion", f"task-completion-extra-{index}", stochastic=True) for index in range(4, 101)]
    )
    results = set_run(results, "task_completion", run_id="task-completion-1", passed=False, completion=False)
    results = set_run(results, "task_completion", run_id="task-completion-2", passed=False, completion=False)
    results = set_run(results, "task_completion", run_id="task-completion-3", passed=False, completion=False)
    baseline = valid_baseline()
    accepted = sequence(baseline.get("accepted"))
    mapping(mapping(accepted[0]).get("metrics"))["taskCompletionRate"] = 1.0

    with pytest.raises(module.EvalThresholdFailure, match="regression"):
        module.enforce_thresholds(results, baseline)


def test_gate_blocks_material_latency_regression_only_when_both_limits_are_exceeded() -> None:
    module = load_module()
    results = set_run(valid_results(), "ontology_warm", latencySeconds=13.0)

    with pytest.raises(module.EvalThresholdFailure, match="ontology-warm-p95"):
        module.enforce_thresholds(results, valid_baseline())


@pytest.mark.parametrize("latency_seconds", [12.0, 10.5])
def test_gate_does_not_block_when_only_one_latency_limit_is_exceeded(latency_seconds: float) -> None:
    module = load_module()
    results = set_run(valid_results(), "ontology_warm", latencySeconds=latency_seconds)

    module.enforce_thresholds(results, valid_baseline())


@pytest.mark.parametrize(
    "forbidden",
    [
        "prompt",
        "resultValue",
        "targetId",
        "endpoint",
        "topology",
        "ownerId",
        "schemaLabel",
        "propertyLabel",
        "credential",
    ],
)
def test_eval_report_rejects_content_bearing_fields(forbidden: str) -> None:
    module = load_module()

    with pytest.raises(module.EvalReportError, match="content-free"):
        module.serialize_report(valid_results(), extra_fields={forbidden: "sensitive"})
