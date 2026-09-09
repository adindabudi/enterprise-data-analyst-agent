#!/usr/bin/env bash
set -euo pipefail

fail() {
    printf '%s\n' "FAIL: $1" >&2
    exit 1
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || fail "required command is unavailable: $1"
}

require_value() {
    local name="$1"
    [[ -n "${!name:-}" ]] || fail "$name must be set"
}

if [[ "${FABRIC_ENABLED:-false}" == "false" ]]; then
    printf '%s\n' "SKIP: Fabric intentionally disabled"
    exit 0
fi
[[ "${FABRIC_ENABLED:-}" == "true" ]] || fail "FABRIC_ENABLED must be true or false"
case "${FABRIC_PROVIDER:-}" in
    semantic_model) ;;
    ontology)
        printf '%s\n' "SKIP: Fabric semantic model provider inactive"
        exit 0
        ;;
    *) fail "enabled Fabric requires FABRIC_PROVIDER=semantic_model" ;;
esac

for command_name in az jq python3 sha256sum stat uv; do
    require_command "$command_name"
done
for name in \
    PRODUCT_AZURE_CONFIG_DIR \
    AZURE_TENANT_ID \
    EDA_MODEL_PROFILE \
    EDA_DEPLOYMENT_ID \
    EDA_COSMOS_ENDPOINT \
    EDA_COSMOS_DATABASE \
    EDA_COSMOS_RUNTIME_CONTAINER \
    FABRIC_CACHE_KEY_ID \
    FABRIC_ACCEPTANCE_PRINCIPAL_ID \
    FABRIC_ACCEPTANCE_JOB_ID \
    FABRIC_PROVIDER_CONTRACT_SHA256 \
    FABRIC_ACCEPTANCE_FIXTURE \
    EDA_FABRIC_ACCEPTANCE_OBSERVATIONS; do
    require_value "$name"
done
[[ "$EDA_MODEL_PROFILE" == "gpt-5.6-terra-medium-v1" ]] \
    || fail "EDA_MODEL_PROFILE must be gpt-5.6-terra-medium-v1"
[[ -d "$PRODUCT_AZURE_CONFIG_DIR" && "$(stat -c '%a' "$PRODUCT_AZURE_CONFIG_DIR")" == "700" ]] \
    || fail "PRODUCT_AZURE_CONFIG_DIR must have mode 0700"
[[ "$(stat -c '%a' "$FABRIC_ACCEPTANCE_FIXTURE")" == "600" ]] \
    || fail "FABRIC_ACCEPTANCE_FIXTURE must have mode 0600"

run_id="${EDA_FABRIC_ACCEPTANCE_RUN_ID:-run_$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')}"
case "$run_id" in
    run_[A-Za-z0-9_-]*) ;;
    *) fail "acceptance run ID is invalid" ;;
esac

export AZURE_CONFIG_DIR="$PRODUCT_AZURE_CONFIG_DIR"
active_tenant="$(az account show --query tenantId --output tsv --only-show-errors)"
[[ "$active_tenant" == "$AZURE_TENANT_ID" ]] || fail "active product tenant does not match"

uv run python scripts/prepare-fabric-acceptance.py \
    --fixture "$FABRIC_ACCEPTANCE_FIXTURE" \
    --run-id "$run_id" \
    --deployment-id "$EDA_DEPLOYMENT_ID" \
    --provider-contract-sha256 "$FABRIC_PROVIDER_CONTRACT_SHA256" \
    --cosmos-endpoint "$EDA_COSMOS_ENDPOINT" \
    --database "$EDA_COSMOS_DATABASE" \
    --container "$EDA_COSMOS_RUNTIME_CONTAINER" \
    --cache-key-id "$FABRIC_CACHE_KEY_ID" \
    --tenant-id "$AZURE_TENANT_ID" \
    --expected-principal-id "$FABRIC_ACCEPTANCE_PRINCIPAL_ID" >/dev/null \
    || fail "unable to prepare encrypted Fabric acceptance input"

resource_group="$(printf '%s' "$FABRIC_ACCEPTANCE_JOB_ID" | cut -d/ -f5)"
job_name="${FABRIC_ACCEPTANCE_JOB_ID##*/}"
[[ -n "$resource_group" && -n "$job_name" ]] || fail "FABRIC_ACCEPTANCE_JOB_ID is invalid"
execution_name="$(az containerapp job start \
    --ids "$FABRIC_ACCEPTANCE_JOB_ID" \
    --env-vars "EDA_FABRIC_ACCEPTANCE_RUN_ID=$run_id" \
    --query name \
    --output tsv \
    --only-show-errors)" || fail "unable to start Fabric acceptance job"
[[ -n "$execution_name" ]] || fail "Fabric acceptance job returned no execution name"

for attempt in $(seq 1 120); do
    execution_state="$(az containerapp job execution show \
        --name "$job_name" \
        --job-execution-name "$execution_name" \
        --resource-group "$resource_group" \
        --query properties.status \
        --output tsv \
        --only-show-errors)" || fail "unable to inspect Fabric acceptance job"
    case "$execution_state" in
        Succeeded) break ;;
        Failed | Stopped | Degraded) fail "Fabric acceptance job failed" ;;
    esac
    [[ "$attempt" -lt 120 ]] || fail "Fabric acceptance job timed out"
    sleep 5
done

[[ -f "$EDA_FABRIC_ACCEPTANCE_OBSERVATIONS" ]] || fail "external Fabric observation artifact is unavailable"
[[ "$(stat -c '%a' "$EDA_FABRIC_ACCEPTANCE_OBSERVATIONS")" == "600" ]] \
    || fail "Fabric observation artifact must have mode 0600"
export EDA_RUN_FABRIC_ACCEPTANCE=1
uv run pytest -m cloud tests/cloud/fabric -q \
    || fail "Fabric cloud acceptance assertions failed"

manifest_path=".artifacts/fabric-acceptance.json"
OBSERVATIONS="$EDA_FABRIC_ACCEPTANCE_OBSERVATIONS" MANIFEST="$manifest_path" RUN_ID="$run_id" python3 <<'PY'
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from tests.cloud.fabric.evidence import FabricCloudObservations

observations = FabricCloudObservations.model_validate_json(Path(os.environ["OBSERVATIONS"]).read_text(encoding="utf-8"))
if observations.run_id != os.environ["RUN_ID"]:
    raise SystemExit("observation run ID does not match the started job")
manifest = {
    "schemaVersion": 1,
    "provider": "semantic_model",
    "state": "passed",
    "topology": "cross_tenant",
    "runId": observations.run_id,
    "deploymentId": observations.deployment_id,
    "providerContractSha256": observations.provider_contract_sha256,
    "modelProfile": observations.model_profile,
    "servedModel": observations.served_model,
    "servedSnapshot": observations.served_snapshot,
    "promptVersion": observations.prompt_version,
    "promptSha256": observations.prompt_sha256,
    "requestOptionsSha256": observations.request_options_sha256,
    "tenantHashes": list(observations.tenant_hashes),
    "tests": observations.controls,
    "startedAt": observations.observed_at.isoformat(),
    "completedAt": datetime.now(UTC).isoformat(),
}
path = Path(os.environ["MANIFEST"])
path.parent.mkdir(parents=True, exist_ok=True)
temporary = path.with_name(f".{path.name}.tmp")
temporary.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
temporary.chmod(0o600)
temporary.replace(path)
PY

uv run python scripts/finalize-fabric-acceptance.py \
    --manifest "$manifest_path" \
    --cosmos-endpoint "$EDA_COSMOS_ENDPOINT" \
    --database "$EDA_COSMOS_DATABASE" \
    --container "$EDA_COSMOS_RUNTIME_CONTAINER" \
    --tenant-id "$AZURE_TENANT_ID" \
    --expected-principal-id "$FABRIC_ACCEPTANCE_PRINCIPAL_ID" >/dev/null \
    || fail "Fabric readiness promotion failed"

printf '%s\n' "PASS: Fabric cross-tenant acceptance promoted run_sha256=$(printf '%s' "$run_id" | sha256sum | cut -d' ' -f1)"
