from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import cast

REQUIRED_FIELDS = {
    "schemaVersion",
    "provider",
    "state",
    "topology",
    "runId",
    "providerContractDigest",
    "authContractDigest",
    "deploymentDigest",
    "fixtureDigest",
    "controls",
}
DIGEST_FIELDS = ("providerContractDigest", "authContractDigest", "deploymentDigest", "fixtureDigest")
DIGEST_PATTERN = re.compile(r"^[a-f0-9]{64}$")
RUN_ID_PATTERN = re.compile(r"^run_[A-Za-z0-9_-]{8,}$")


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare an offline Fabric ontology readiness finalization request.")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def canonical_digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def validate_report(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("acceptance report has missing or unknown fields")
    report = {key: item for key, item in cast(Mapping[str, object], value).items()}
    if set(report) != REQUIRED_FIELDS:
        raise ValueError("acceptance report has missing or unknown fields")
    if report["schemaVersion"] != 1 or report["provider"] != "ontology":
        raise ValueError("acceptance report is not an ontology report")
    if report["state"] != "configured" or report["topology"] != "cross_tenant":
        raise ValueError("acceptance report must be cross_tenant and configured")
    run_id = report["runId"]
    if not isinstance(run_id, str):
        raise ValueError("acceptance report runId is invalid")
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError("acceptance report runId is invalid")
    for field in DIGEST_FIELDS:
        digest = report[field]
        if not isinstance(digest, str):
            raise ValueError("acceptance report contains an invalid digest")
        if not DIGEST_PATTERN.fullmatch(digest):
            raise ValueError("acceptance report contains an invalid digest")
    controls = report["controls"]
    if not isinstance(controls, Mapping):
        raise ValueError("acceptance report controls must all pass")
    control_statuses = cast(Mapping[str, object], controls)
    if not control_statuses or any(
        not isinstance(status, str) or status != "passed" for status in control_statuses.values()
    ):
        raise ValueError("acceptance report controls must all pass")
    return report


def write_json_atomically(path: Path, value: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    arguments = parse_arguments()
    try:
        report = validate_report(json.loads(arguments.report.read_text(encoding="utf-8")))
        prepared = {
            "fromState": "configured",
            "provider": "ontology",
            "reportSha256": canonical_digest(report),
            "transition": "readiness_finalization_pending",
        }
        write_json_atomically(arguments.output, prepared)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        arguments.output.unlink(missing_ok=True)
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: offline Fabric ontology readiness finalization prepared")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
