#!/bin/sh
set -eu

fail() {
    printf '%s\n' "FAIL: $1" >&2
    exit 1
}

if [ "${FABRIC_ENABLED+x}" = 'x' ]; then
    if [ "${FABRIC_ENABLED}" = 'false' ]; then
        printf '%s\n' 'SKIP: Fabric ontology intentionally disabled'
        exit 0
    fi
    [ "${FABRIC_PROVIDER:-}" = 'ontology' ] || fail 'Fabric ontology provider is inactive'
fi

REPORT=''
OUTPUT='.artifacts/fabric-ontology-smoke.json'

while [ "$#" -gt 0 ]; do
    case "$1" in
        --report) REPORT="${2:-}"; shift 2 ;;
        --output) OUTPUT="${2:-}"; shift 2 ;;
        --help)
            printf '%s\n' 'Usage: run-fabric-ontology-smoke.sh --report PATH [--output PATH]'
            exit 0
            ;;
        *) fail "unsupported argument: $1" ;;
    esac
done

[ -n "$REPORT" ] || fail '--report is required'
[ -r "$REPORT" ] || fail '--report must be readable'

python3 - "$REPORT" "$OUTPUT" <<'PY'
import hashlib
import json
import re
import sys
from pathlib import Path

report_path, output_path = map(Path, sys.argv[1:])
required = {
    "schemaVersion", "provider", "state", "topology", "runId", "providerContractDigest",
    "authContractDigest", "deploymentDigest", "fixtureDigest", "controls",
}
digest_fields = ("providerContractDigest", "authContractDigest", "deploymentDigest", "fixtureDigest")

try:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if not isinstance(report, dict) or set(report) != required:
        raise ValueError("smoke report has missing or unknown fields")
    if report["schemaVersion"] != 1 or report["provider"] != "ontology":
        raise ValueError("smoke report is not an ontology report")
    if report["state"] != "smoke_passed" or report["topology"] != "same_tenant":
        raise ValueError("smoke report must be same_tenant and smoke_passed")
    if not isinstance(report["runId"], str) or not re.fullmatch(r"run_[A-Za-z0-9_-]{8,}", report["runId"]):
        raise ValueError("smoke report runId is invalid")
    if any(not isinstance(report[name], str) or not re.fullmatch(r"[a-f0-9]{64}", report[name]) for name in digest_fields):
        raise ValueError("smoke report contains an invalid digest")
    if not isinstance(report["controls"], dict) or not report["controls"] or set(report["controls"].values()) != {"passed"}:
        raise ValueError("smoke report controls must all pass")
    encoded = json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    evidence = {
        "provider": "ontology",
        "reportSha256": hashlib.sha256(encoded).hexdigest(),
        "state": "smoke_passed",
        "topology": "same_tenant",
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(f".{output_path.name}.tmp")
    temporary.write_text(json.dumps(evidence, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    temporary.replace(output_path)
except (OSError, ValueError, json.JSONDecodeError) as error:
    output_path.unlink(missing_ok=True)
    print(f"FAIL: {error}", file=sys.stderr)
    raise SystemExit(1)
PY

printf '%s\n' 'PASS: Fabric ontology same-tenant smoke evidence prepared'