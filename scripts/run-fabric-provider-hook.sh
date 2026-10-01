#!/usr/bin/env bash
set -euo pipefail

fail() {
    printf '%s\n' "FAIL: $1" >&2
    exit 1
}

phase="${1:-}"
[[ "$phase" == "preprovision" ]] || fail "usage: run-fabric-provider-hook.sh preprovision"

if [[ "${FABRIC_ENABLED:-false}" == "false" ]]; then
    printf '%s\n' "SKIP: Fabric intentionally disabled"
    exit 0
fi
[[ "${FABRIC_ENABLED:-}" == "true" ]] || fail "FABRIC_ENABLED must be true or false"
[[ "${FABRIC_PROVIDER:-}" == "ontology" ]] || fail "enabled Fabric requires FABRIC_PROVIDER=ontology"
[[ -n "${FABRIC_ONTOLOGIES_JSON:-}" && "${FABRIC_ONTOLOGIES_JSON}" != "{}" ]] \
    || fail "ontology provider requires FABRIC_ONTOLOGIES_JSON"
printf '%s\n' "PASS: ontology provider intent validated"
