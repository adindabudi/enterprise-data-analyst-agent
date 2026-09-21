"""Read-only GQL regression runner; measures graph correctness, not end-to-end agent quality."""

from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import UUID

import httpx

FABRIC = "https://api.fabric.microsoft.com"


def object_value(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("expected JSON object")
    return cast(dict[str, object], value)


def rows_value(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise ValueError("expected row array")
    return [object_value(row) for row in cast(list[object], value)]


def equal_value(actual: object, expected: object, *, tolerance: float) -> bool:
    if expected is None or isinstance(expected, (str, bool)):
        return type(actual) is type(expected) and actual == expected
    if isinstance(expected, (int, float)):
        if isinstance(actual, bool) or not isinstance(actual, (int, float)):
            return False
        return math.isfinite(float(actual)) and math.isclose(
            float(actual), float(expected), abs_tol=tolerance, rel_tol=tolerance
        )
    return actual == expected


def compare_rows(
    actual: list[dict[str, object]], expected: list[dict[str, object]], *, tolerance: float = 1e-6
) -> bool:
    """Compare as a multiset; consume matches so duplicates cannot mask omitted rows."""
    if len(actual) != len(expected):
        return False
    remaining = list(actual)
    for wanted in expected:
        for index, row in enumerate(remaining):
            if row.keys() == wanted.keys() and all(
                equal_value(row[key], value, tolerance=tolerance) for key, value in wanted.items()
            ):
                remaining.pop(index)
                break
        else:
            return False
    return not remaining


def token(subscription: str) -> str:
    az = shutil.which("az")
    if az is None:
        raise RuntimeError("Azure CLI is required; sign in explicitly to the intended subscription")
    completed = subprocess.run(  # noqa: S603 - fixed executable and scoped argument list
        [
            az,
            "account",
            "get-access-token",
            "--subscription",
            str(UUID(subscription)),
            "--resource",
            FABRIC,
            "-o",
            "json",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    value = object_value(json.loads(completed.stdout))
    if value.get("subscription") != subscription:
        raise ValueError("Azure CLI token subscription does not match the selected target")
    access_token = value.get("accessToken")
    if not isinstance(access_token, str) or not access_token:
        raise ValueError("Azure CLI did not return an access token")
    return access_token


def execute_cases(client: httpx.Client, endpoint: str, cases: list[dict[str, object]]) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for case in cases:
        query = case.get("query")
        if not isinstance(query, str) or not query.lstrip().upper().startswith("MATCH ") or len(query) > 8_000:
            raise ValueError("each case must contain a bounded read-only MATCH query")
        expected = rows_value(case["expected"])
        started = time.perf_counter()
        response = client.post(endpoint, json={"query": query})
        elapsed = round(time.perf_counter() - started, 3)
        response.raise_for_status()
        body = object_value(response.json())
        status = object_value(body["status"])
        code = status.get("code")
        actual = rows_value(object_value(body["result"])["data"]) if code == "00000" else []
        results.append(
            {
                "id": case["id"],
                "query": query,
                "seconds": elapsed,
                "status": code,
                "pass": code == "00000" and compare_rows(actual, expected),
                "actual": actual,
                "expected": expected,
                "failure": status if code != "00000" else None,
            }
        )
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subscription", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--graph", required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = cast(Path, args.output)
    if output.exists():
        raise FileExistsError("choose a new observation output path; previous evidence is immutable")
    cases = rows_value(json.loads(cast(Path, args.cases).read_text(encoding="utf-8")))
    if not cases:
        raise ValueError("the regression suite must contain at least one case")
    endpoint = f"{FABRIC}/v1/workspaces/{UUID(args.workspace)}/GraphModels/{UUID(args.graph)}/executeQuery?preview=true"
    with httpx.Client(
        headers={"Authorization": f"Bearer {token(args.subscription)}"},
        timeout=httpx.Timeout(120, connect=10),
        follow_redirects=False,
    ) as client:
        results = execute_cases(client, endpoint, cases)
    report = {
        "scope": "graph-only; does not establish natural-language agent or document-retrieval correctness",
        "observedAt": datetime.now(UTC).isoformat(),
        "passed": sum(result["pass"] is True for result in results),
        "total": len(results),
        "results": results,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"GQL cases passed: {report['passed']}/{report['total']}; evidence: {output}")
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
