#!/usr/bin/env bash
set -euo pipefail

fail() {
    printf '%s\n' "FAIL: $1" >&2
    exit 1
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || fail "required command is unavailable: $1"
}

for command_name in az azd base64; do
    require_command "$command_name"
done

if [[ "${DOCUMENTS_ENABLED:-false}" == "false" ]]; then
    printf '%s\n' "SKIP: Document Pack intentionally disabled"
    exit 0
fi
[[ "${DOCUMENTS_ENABLED:-}" == "true" ]] || fail "DOCUMENTS_ENABLED must be true or false"

azd_values="$(azd env get-values)" || fail "unable to read azd environment values"
azd_value() {
    local name="$1"
    local value
    value="$(printf '%s\n' "$azd_values" | sed -n "s/^${name}=//p" | head -n 1)"
    case "$value" in
        \"*\") value="${value#\"}"; value="${value%\"}" ;;
    esac
    [[ -n "$value" ]] || fail "azd output ${name} must be set"
    printf '%s\n' "$value"
}

cleanup_job_id="$(azd_value CLEANUP_JOB_ID)"
worker_image="$(azd_value EDA_WORKER_IMAGE)"
worker_identity_client_id="$(azd_value WORKER_IDENTITY_CLIENT_ID)"
cosmos_endpoint="$(azd_value COSMOS_ENDPOINT)"
resource_group="$(azd_value AZURE_RESOURCE_GROUP)"
contract_path="${EDA_DOCUMENT_IMAGE_CONTRACT_PATH:-.artifacts/document-image-contract.json}"
[[ -f "$contract_path" ]] || fail "Document Pack image contract is unavailable"
case "$worker_image" in
    *@sha256:????????????????????????????????????????????????????????????????) ;;
    *) fail "worker image must be pinned by SHA-256 digest" ;;
esac

contract_base64="$(base64 --wrap=0 "$contract_path")" || fail "unable to encode Document Pack image contract"
execution_name="$(az containerapp job start \
    --ids "$cleanup_job_id" \
    --image "$worker_image" \
    --container-name "cleanup" \
    --cpu "1" \
    --memory "2Gi" \
    --command "eda-worker" \
    --args "publish-documents" \
    --env-vars \
        "EDA_MANAGED_IDENTITY_CLIENT_ID=$worker_identity_client_id" \
        "EDA_COSMOS_ENDPOINT=$cosmos_endpoint" \
        "EDA_COSMOS_DATABASE=enterprise-data-analyst" \
        "EDA_COSMOS_RUNTIME_CONTAINER=runtime" \
        "EDA_DOCUMENT_IMAGE_CONTRACT_BASE64=$contract_base64" \
    --query name \
    --output tsv \
    --only-show-errors)" || fail "unable to start Document Pack publication job"
[[ -n "$execution_name" ]] || fail "Document Pack publication job returned no execution name"

job_name="${cleanup_job_id##*/}"
for _attempt in $(seq 1 120); do
    status="$(az containerapp job execution show \
        --name "$job_name" \
        --job-execution-name "$execution_name" \
        --resource-group "$resource_group" \
        --query properties.status \
        --output tsv \
        --only-show-errors)" || fail "unable to inspect Document Pack publication job"
    case "$status" in
        Succeeded) printf '%s\n' "PASS: Document Pack contract published from private job"; exit 0 ;;
        Failed) fail "Document Pack publication job failed" ;;
        Running | Processing | Pending) sleep 5 ;;
        *) fail "Document Pack publication job returned an unknown status" ;;
    esac
done
fail "Document Pack publication job timed out"