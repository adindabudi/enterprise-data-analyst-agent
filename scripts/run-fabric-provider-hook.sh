#!/usr/bin/env bash
set -euo pipefail

fail() {
    printf '%s\n' "FAIL: $1" >&2
    exit 1
}

phase="${1:-}"
case "$phase" in
    preprovision | postprovision | postdeploy) ;;
    *) fail "usage: run-fabric-provider-hook.sh preprovision|postprovision|postdeploy" ;;
esac

if [[ "${FABRIC_ENABLED:-false}" == "false" ]]; then
    printf '%s\n' "SKIP: Fabric intentionally disabled"
    exit 0
fi
[[ "${FABRIC_ENABLED:-}" == "true" ]] || fail "FABRIC_ENABLED must be true or false"
provider="${FABRIC_PROVIDER:-}"
[[ "$provider" == "semantic_model" || "$provider" == "ontology" ]] || fail "enabled Fabric requires a known provider"

if [[ "$phase" == "preprovision" ]]; then
    if [[ "$provider" == "semantic_model" ]]; then
        exec ./scripts/doctor-fabric.sh --phase predeploy
    fi
    [[ -n "${FABRIC_ONTOLOGIES_JSON:-}" && "${FABRIC_ONTOLOGIES_JSON}" != "{}" ]] \
        || fail "ontology provider requires FABRIC_ONTOLOGIES_JSON"
    printf '%s\n' "PASS: ontology provider intent validated"
    exit 0
fi

if [[ "$phase" == "postprovision" ]]; then
    if [[ "${FABRIC_TOPOLOGY:-cross_tenant}" == "same_tenant_smoke" ]]; then
        printf '%s\n' "SKIP: production Fabric proof unavailable in same-tenant smoke topology"
        exit 0
    fi
    if [[ "$provider" == "semantic_model" ]]; then
        exec ./scripts/doctor-fabric.sh --phase postdeploy
    fi
    ./scripts/doctor-fabric-ontology.sh
    exec uv run python scripts/probe-fabric-ontology-auth.py
fi

if [[ "${FABRIC_ACCEPTANCE_ENABLED:-false}" == "false" ]]; then
    printf '%s\n' "SKIP: Fabric acceptance intentionally disabled"
    exit 0
fi
[[ "${FABRIC_ACCEPTANCE_ENABLED:-}" == "true" ]] \
    || fail "FABRIC_ACCEPTANCE_ENABLED must be true or false"

if [[ "$provider" == "semantic_model" ]]; then
    uv run python scripts/check-fabric-contract.py
    uv run python scripts/publish-fabric-contract.py
    exec ./scripts/run-fabric-acceptance.sh
fi

: "${FABRIC_ONTOLOGY_CATALOG_PATH:?FABRIC_ONTOLOGY_CATALOG_PATH must be set}"
: "${FABRIC_ONTOLOGY_INSPECTION_PATH:?FABRIC_ONTOLOGY_INSPECTION_PATH must be set}"
: "${FABRIC_ONTOLOGY_PROVIDER_CONTRACT_PATH:=.artifacts/fabric-ontology-provider-contract.json}"
: "${FABRIC_ONTOLOGY_PUBLICATION_PATH:=.artifacts/fabric-ontology-publication.json}"
: "${FABRIC_ONTOLOGY_ACCEPTANCE_REPORT:?FABRIC_ONTOLOGY_ACCEPTANCE_REPORT must be set}"
: "${FABRIC_ONTOLOGY_WORKER_EVIDENCE:=.artifacts/fabric-ontology-worker-evidence.json}"

uv run python scripts/check-fabric-ontology-contract.py \
    --catalog "$FABRIC_ONTOLOGY_CATALOG_PATH" \
    --inspection "$FABRIC_ONTOLOGY_INSPECTION_PATH" \
    --output "$FABRIC_ONTOLOGY_PROVIDER_CONTRACT_PATH"
uv run python scripts/publish-fabric-ontology-contract.py \
    --provider-contract "$FABRIC_ONTOLOGY_PROVIDER_CONTRACT_PATH" \
    --publish \
    --output "$FABRIC_ONTOLOGY_PUBLICATION_PATH"
exec ./scripts/run-fabric-ontology-acceptance.sh \
    --report "$FABRIC_ONTOLOGY_ACCEPTANCE_REPORT" \
    --evidence "$FABRIC_ONTOLOGY_WORKER_EVIDENCE"
