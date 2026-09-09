#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONTRACT="$ROOT/.artifacts/document-image-contract.json"

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

azd_value() {
    local name="$1"
    local value
    value="$(printf '%s\n' "$azd_values" | sed -n "s/^${name}=//p" | head -n 1)"
    case "$value" in
        \"*\") value="${value#\"}"; value="${value%\"}" ;;
    esac
    [[ -n "$value" ]] || fail "azd output $name must be set"
    printf '%s\n' "$value"
}

restart_active_revision() {
    local app_id="$1"
    local resource_group app_name revision
    resource_group="$(printf '%s' "$app_id" | cut -d/ -f5)"
    app_name="${app_id##*/}"
    [[ -n "$resource_group" && -n "$app_name" ]] || fail "Container App resource ID is invalid"
    revision="$(az containerapp revision list \
        --name "$app_name" \
        --resource-group "$resource_group" \
        --query '[?properties.active].name | [0]' \
        --output tsv \
        --only-show-errors)" || fail "unable to resolve active Container App revision"
    [[ -n "$revision" ]] || fail "Container App has no active revision"
    az containerapp revision restart \
        --name "$app_name" \
        --resource-group "$resource_group" \
        --revision "$revision" \
        --only-show-errors \
        --output none || fail "unable to restart Container App revision"
}

if [[ "${DOCUMENTS_ENABLED:-false}" == "false" ]]; then
    printf '%s\n' "SKIP: Document Pack intentionally disabled"
    exit 0
fi
[[ "${DOCUMENTS_ENABLED:-}" == "true" ]] || fail "DOCUMENTS_ENABLED must be true or false"
cd "$ROOT"

for command_name in az azd curl jq python3 sha256sum stat uv; do
    require_command "$command_name"
done
for name in \
    PRODUCT_AZURE_CONFIG_DIR \
    AZURE_TENANT_ID \
    EDA_DEPLOYMENT_ID \
    EDA_COSMOS_ENDPOINT \
    EDA_COSMOS_DATABASE \
    EDA_COSMOS_RUNTIME_CONTAINER \
    EDA_WORKER_IMAGE \
    EDA_DOCUMENT_TERMS_ACCEPTED \
    EDA_DOCUMENT_ACCEPTANCE_OBSERVATIONS \
    EDA_SANDBOX_BENCHMARK_RESULTS; do
    require_value "$name"
done
acceptance_principal="${EDA_ACCEPTANCE_PRINCIPAL_ID:-${FABRIC_ACCEPTANCE_PRINCIPAL_ID:-}}"
[[ -n "$acceptance_principal" ]] || fail "EDA_ACCEPTANCE_PRINCIPAL_ID must be set"
[[ -d "$PRODUCT_AZURE_CONFIG_DIR" && "$(stat -c '%a' "$PRODUCT_AZURE_CONFIG_DIR")" == "700" ]] \
    || fail "PRODUCT_AZURE_CONFIG_DIR must have mode 0700"
for evidence_path in "$EDA_DOCUMENT_ACCEPTANCE_OBSERVATIONS" "$EDA_SANDBOX_BENCHMARK_RESULTS"; do
    [[ -f "$evidence_path" && "$(stat -c '%a' "$evidence_path")" == "600" ]] \
        || fail "Document Pack evidence files must be regular mode-0600 files"
done

expected_terms="$(sha256sum "$ROOT/skills.lock.json" | cut -d' ' -f1)"
[[ "$EDA_DOCUMENT_TERMS_ACCEPTED" == "$expected_terms" ]] \
    || fail "EDA_DOCUMENT_TERMS_ACCEPTED does not match the reviewed skills.lock.json"

export AZURE_CONFIG_DIR="$PRODUCT_AZURE_CONFIG_DIR"
active_tenant="$(az account show --query tenantId --output tsv --only-show-errors)"
[[ "$active_tenant" == "$AZURE_TENANT_ID" ]] || fail "active product tenant does not match"
azd_values="$(azd env get-values)" || fail "unable to read azd environment values"
export EDA_SANDBOX_IMAGE="${EDA_SANDBOX_IMAGE:-$(azd_value EDA_SANDBOX_IMAGE)}"
api_app_id="${EDA_API_APP_ID:-$(azd_value API_APP_ID)}"
worker_app_id="${EDA_WORKER_APP_ID:-$(azd_value WORKER_APP_ID)}"
app_url="${EDA_APP_URL:-$(azd_value API_URL)}"
[[ "$app_url" == https://* ]] || fail "deployed API URL must use HTTPS"

"$ROOT/scripts/doctor-documents.sh" --phase postdeploy --output "$CONTRACT" >/dev/null \
    || fail "deployed Document Pack image verification failed"
uv run python "$ROOT/scripts/publish-document-contract.py" \
    --contract "$CONTRACT" \
    --cosmos-endpoint "$EDA_COSMOS_ENDPOINT" \
    --database "$EDA_COSMOS_DATABASE" \
    --container "$EDA_COSMOS_RUNTIME_CONTAINER" \
    --tenant-id "$AZURE_TENANT_ID" \
    --expected-principal-id "$acceptance_principal" >/dev/null \
    || fail "Document Pack configured-state publication failed"

OBSERVATIONS="$EDA_DOCUMENT_ACCEPTANCE_OBSERVATIONS" \
CONTRACT="$CONTRACT" \
BENCHMARK="$EDA_SANDBOX_BENCHMARK_RESULTS" \
uv run python - <<'PY' || fail "Document Pack observations do not bind to the deployed contract and benchmark"
import hashlib
import os
from pathlib import Path

from eda_worker.acceptance.documents import (
    DocumentAcceptanceObservations,
    DocumentImageContract,
    document_contract_sha256,
)

observations = DocumentAcceptanceObservations.model_validate_json(
    Path(os.environ["OBSERVATIONS"]).read_text(encoding="utf-8")
)
contract = DocumentImageContract.model_validate_json(Path(os.environ["CONTRACT"]).read_text(encoding="utf-8"))
benchmark_sha256 = hashlib.sha256(Path(os.environ["BENCHMARK"]).read_bytes()).hexdigest()
if observations.contract_sha256 != document_contract_sha256(contract):
    raise SystemExit(1)
if observations.benchmark_sha256 != benchmark_sha256:
    raise SystemExit(1)
PY

uv run pytest "$ROOT/tests/acceptance/test_document_pack.py" -q \
    || fail "Document Pack local vertical slice failed"
export EDA_RUN_DOCUMENT_ACCEPTANCE=1
uv run pytest -m cloud "$ROOT/tests/cloud/documents" -q \
    || fail "Document Pack cloud acceptance assertions failed"
uv run pytest "$ROOT/tests/benchmark/test_document_concurrency.py" -q \
    || fail "Document Pack measured concurrency gate failed"

uv run python "$ROOT/scripts/finalize-document-acceptance.py" \
    --observations "$EDA_DOCUMENT_ACCEPTANCE_OBSERVATIONS" \
    --cosmos-endpoint "$EDA_COSMOS_ENDPOINT" \
    --database "$EDA_COSMOS_DATABASE" \
    --container "$EDA_COSMOS_RUNTIME_CONTAINER" \
    --tenant-id "$AZURE_TENANT_ID" \
    --expected-principal-id "$acceptance_principal" >/dev/null \
    || fail "Document Pack readiness promotion failed"

restart_active_revision "$worker_app_id"
restart_active_revision "$api_app_id"
for attempt in $(seq 1 60); do
    documents_state="$(curl --fail --silent --show-error "$app_url/health/ready" | jq -r '.featurePacks.documents // empty')" \
        || documents_state=""
    [[ "$documents_state" == "ready" ]] && break
    [[ "$attempt" -lt 60 ]] || fail "deployed API did not report documents=ready after promotion"
    sleep 5
done

evidence_sha256="$(sha256sum "$EDA_DOCUMENT_ACCEPTANCE_OBSERVATIONS" | cut -d' ' -f1)"
printf '%s\n' "PASS: Document Pack deployed acceptance promoted evidence_file_sha256=$evidence_sha256"
