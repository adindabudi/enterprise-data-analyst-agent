from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import httpx


def load() -> Any:
    script = Path(__file__).resolve().parents[1] / "energy_demo_live.py"
    spec = importlib.util.spec_from_file_location("energy_demo_live", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_comparison_preserves_nulls_duplicates_and_complete_result_shape() -> None:
    compare = load().compare_rows
    assert compare([{"n": 2}, {"n": 1.00000001}], [{"n": 1}, {"n": 2}])
    assert not compare([{"n": 1}, {"n": 1}], [{"n": 1}, {"n": 2}])
    assert not compare([{"n": 0}], [{"n": None}])
    assert not compare([{"n": False}], [{"n": 0}])
    assert not compare([{"n": float("nan")}], [{"n": 1}])
    assert not compare([{"n": "2"}], [{"n": 2}])
    assert not compare([{"n": 2, "extra": 3}], [{"n": 2}])


def test_runner_fails_closed_on_gql_error_even_when_zero_rows_expected() -> None:
    module = load()

    def respond(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["query"] == "MATCH (n:Well) RETURN n.WellId"
        return httpx.Response(200, json={"status": {"code": "42000", "description": "parse error"}})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        results = module.execute_cases(
            client,
            "https://example.invalid/query",
            [{"id": "empty", "query": "MATCH (n:Well) RETURN n.WellId", "expected": []}],
        )
    assert results[0]["pass"] is False
    assert results[0]["failure"]["code"] == "42000"
