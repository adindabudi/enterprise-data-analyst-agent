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
    [ "${FABRIC_ENABLED}" = 'true' ] || fail 'FABRIC_ENABLED must be true or false'
    if [ "${FABRIC_PROVIDER:-}" = 'semantic_model' ]; then
        printf '%s\n' 'SKIP: Fabric ontology provider inactive'
        exit 0
    fi
    [ "${FABRIC_PROVIDER:-}" = 'ontology' ] || fail 'enabled Fabric ontology requires FABRIC_PROVIDER=ontology'
fi

REPORT=''
EVIDENCE=''
OUTPUT='.artifacts/fabric-ontology-acceptance.json'

while [ "$#" -gt 0 ]; do
    case "$1" in
        --report) REPORT="${2:-}"; shift 2 ;;
        --evidence) EVIDENCE="${2:-}"; shift 2 ;;
        --output) OUTPUT="${2:-}"; shift 2 ;;
        --help)
            printf '%s\n' 'Usage: run-fabric-ontology-acceptance.sh --report PATH --evidence PATH [--output PATH]'
            exit 0
            ;;
        *) fail "unsupported argument: $1" ;;
    esac
done

[ -n "$REPORT" ] || fail '--report is required'
[ -r "$REPORT" ] || fail '--report must be readable'
[ -n "$EVIDENCE" ] || fail '--evidence is required'

python3 - "$REPORT" "$EVIDENCE" "$OUTPUT" <<'PY'
import json
import re
import sys
from pathlib import Path

report_path, evidence_path, output_path = map(Path, sys.argv[1:])
required = {
    "schemaVersion", "provider", "state", "topology", "runId", "providerContractDigest",
    "authContractDigest", "deploymentDigest", "fixtureDigest", "controls",
}
digest_fields = ("providerContractDigest", "authContractDigest", "deploymentDigest", "fixtureDigest")

try:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if not isinstance(report, dict) or set(report) != required:
        raise ValueError("acceptance report has missing or unknown fields")
    if report["schemaVersion"] != 1 or report["provider"] != "ontology":
        raise ValueError("acceptance report is not an ontology report")
    if report["state"] != "configured" or report["topology"] != "cross_tenant":
        raise ValueError("acceptance report must be cross_tenant and configured")
    if not isinstance(report["runId"], str) or not re.fullmatch(r"run_[A-Za-z0-9_-]{8,}", report["runId"]):
        raise ValueError("acceptance report runId is invalid")
    if any(not isinstance(report[name], str) or not re.fullmatch(r"[a-f0-9]{64}", report[name]) for name in digest_fields):
        raise ValueError("acceptance report contains an invalid digest")
    if not isinstance(report["controls"], dict) or not report["controls"] or set(report["controls"].values()) != {"passed"}:
        raise ValueError("acceptance report controls must all pass")
    evidence = {
        "authContractDigest": report["authContractDigest"],
        "provider": "ontology",
        "providerContractDigest": report["providerContractDigest"],
        "runId": report["runId"],
        "state": "configured",
        "topology": "cross_tenant",
    }
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(json.dumps(evidence, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
except (OSError, ValueError, json.JSONDecodeError) as error:
    evidence_path.unlink(missing_ok=True)
    output_path.unlink(missing_ok=True)
    print(f"FAIL: {error}", file=sys.stderr)
    raise SystemExit(1)
PY

if ! eda-worker accept-fabric --provider ontology --evidence "$EVIDENCE" >/dev/null 2>&1; then
    rm -f "$EVIDENCE" "$OUTPUT"
    fail 'Fabric ontology acceptance command rejected evidence'
fi

python3 - "$REPORT" "$EVIDENCE" "$OUTPUT" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

report_path, evidence_path, output_path = map(Path, sys.argv[1:])
try:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    digest = lambda value: hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()
    output = {
        "evidenceSha256": digest(evidence),
        "provider": "ontology",
        "reportSha256": digest(report),
        "state": "configured",
        "topology": "cross_tenant",
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(f".{output_path.name}.tmp")
    temporary.write_text(json.dumps(output, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    temporary.replace(output_path)
except (OSError, ValueError, json.JSONDecodeError) as error:
    output_path.unlink(missing_ok=True)
    print(f"FAIL: {error}", file=sys.stderr)
    raise SystemExit(1)
PY

printf '%s\n' 'PASS: Fabric ontology cross-tenant acceptance evidence prepared'