#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT_ALLOWLIST_REGEX='^(resource_group_name|deployment_id|api_url|profile|model_profile|model_deployment_name|project_endpoint|container_registry_id|container_registry_login_server|cosmos_account_id|cosmos_endpoint|storage_account_id|storage_blob_endpoint|redis_cluster_id|redis_hostname|redis_port|web_identity_id|web_identity_client_id|web_identity_principal_id|worker_identity_id|worker_identity_client_id|worker_identity_principal_id|session_init_identity_id|session_init_identity_client_id|session_init_identity_principal_id|virtual_network_id|aca_subnet_id|sandbox_subnet_id|foundry_agent_subnet_id|private_endpoints_subnet_id|log_analytics_workspace_id|app_insights_id|container_apps_environment_id|api_app_id|cleanup_job_id|fabric_vault_url|sandbox_group_id|budget_action_group_id)$'
SECRET_NAME_REGEX='(connection|string|key|password|secret|token|shared_key)'

fail() {
    printf '%s\n' "FAIL: $1" >&2
    exit 1
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || fail "required command is unavailable: $1"
}

run_step() {
    "$@"
}

azd_values() {
    azd env get-values
}

azd_get_value() {
    local name="$1"
    local value
    value="$(azd_values | sed -n "s/^${name}=//p" | head -n 1)"
    case "$value" in
        \"*\") value="${value#\"}"; value="${value%\"}" ;;
    esac
    printf '%s\n' "$value"
}

azd_require_value() {
    local name="$1"
    local value
    value="$(azd_get_value "$name")"
    [[ -n "$value" ]] || fail "azd output $name must be set"
    printf '%s\n' "$value"
}

terraform_apply() {
    terraform -chdir="$ROOT/infra/terraform" apply -auto-approve -state="$TF_STATE_PATH" "$@"
}

require_value() {
    local name="$1"
    [[ -n "${!name:-}" ]] || fail "$name must be set"
}

write_runtime_variables() {
    local destination="$1"
    local entra_client_id fabric_client_id acceptance_principal_id
    entra_client_id="$(azd_require_value ENTRA_CLIENT_ID)"
    fabric_client_id="${FABRIC_CLIENT_ID:-}"
    if [[ "$FABRIC_ENABLED" == "true" ]]; then
        fabric_client_id="${fabric_client_id:-$(azd_require_value FABRIC_CLIENT_ID)}"
    fi
    acceptance_principal_id="${EDA_ACCEPTANCE_PRINCIPAL_ID:-${FABRIC_ACCEPTANCE_PRINCIPAL_ID:-}}"
    [[ -n "$acceptance_principal_id" ]] || fail "EDA_ACCEPTANCE_PRINCIPAL_ID must be set"
    jq -n \
        --arg environmentName "$AZURE_ENV_NAME" \
        --arg resourceGroupName "$AZURE_RESOURCE_GROUP" \
        --arg location "$AZURE_LOCATION" \
        --arg principalId "$AZURE_PRINCIPAL_ID" \
        --arg entraClientId "$entra_client_id" \
        --arg entraTenantId "$AZURE_TENANT_ID" \
        --arg modelProfile "$EDA_MODEL_PROFILE" \
        --arg fabricProvider "$FABRIC_PROVIDER" \
        --arg fabricTenantId "${FABRIC_TENANT_ID:-}" \
        --arg fabricClientId "$fabric_client_id" \
        --arg fabricOntologiesJson "${FABRIC_ONTOLOGIES_JSON:-{}}" \
        --arg acceptancePrincipalId "$acceptance_principal_id" \
        --argjson fabricEnabled "$FABRIC_ENABLED" \
        --argjson documentsEnabled "$DOCUMENTS_ENABLED" \
        --argjson powerBiProjectEnabled "$POWERBI_PROJECT_ENABLED" \
        '{
            environment_name: $environmentName,
            resource_group_name: $resourceGroupName,
            location: $location,
            principal_id: $principalId,
            entra_client_id: $entraClientId,
            entra_tenant_id: $entraTenantId,
            model_profile: $modelProfile,
            fabric_enabled: $fabricEnabled,
            fabric_provider: $fabricProvider,
            fabric_tenant_id: $fabricTenantId,
            fabric_client_id: $fabricClientId,
            fabric_ontologies_json: $fabricOntologiesJson,
            acceptance_principal_id: $acceptancePrincipalId,
            documents_enabled: $documentsEnabled,
            powerbi_project_enabled: $powerBiProjectEnabled
        }' > "$destination"
    chmod 600 "$destination"
}

map_terraform_outputs_to_azd() {
    local output_json="$1"
    local names_file
    names_file="$(mktemp)"
    jq -r 'keys[]' "$output_json" > "$names_file"
    while IFS= read -r output_name; do
        [[ "$output_name" =~ $OUTPUT_ALLOWLIST_REGEX ]] || fail "unknown Terraform output: $output_name"
        [[ ! "$output_name" =~ $SECRET_NAME_REGEX ]] || fail "secret-looking Terraform output is not allowed: $output_name"
    done < "$names_file"
    rm -f "$names_file"

    local required_output
    for required_output in \
        resource_group_name \
        deployment_id \
        api_url \
        profile \
        model_profile \
        model_deployment_name \
        project_endpoint \
        container_registry_id \
        container_registry_login_server \
        cosmos_account_id \
        cosmos_endpoint \
        storage_account_id \
        storage_blob_endpoint \
        redis_cluster_id \
        redis_hostname \
        redis_port \
        web_identity_id \
        web_identity_client_id \
        web_identity_principal_id \
        worker_identity_id \
        worker_identity_client_id \
        worker_identity_principal_id \
        session_init_identity_id \
        container_apps_environment_id \
        api_app_id \
        cleanup_job_id; do
        jq -e --arg name "$required_output" \
            '.[$name] != null and .[$name].sensitive != true and .[$name].value != null and (.[$name].value | tostring | length > 0)' \
            "$output_json" >/dev/null \
            || fail "required Terraform output is missing, sensitive, or empty: $required_output"
    done

    azd env set AZURE_RESOURCE_GROUP "$(jq -r '.resource_group_name.value' "$output_json")" >/dev/null
    azd env set DEPLOYMENT_ID "$(jq -r '.deployment_id.value' "$output_json")" >/dev/null
    azd env set EDA_DEPLOYMENT_ID "$(jq -r '.deployment_id.value' "$output_json")" >/dev/null
    azd env set API_URL "$(jq -r '.api_url.value' "$output_json")" >/dev/null
    azd env set PROFILE "$(jq -r '.profile.value' "$output_json")" >/dev/null
    azd env set EDA_MODEL_PROFILE "$(jq -r '.model_profile.value' "$output_json")" >/dev/null
    azd env set AZURE_AI_MODEL_DEPLOYMENT_NAME "$(jq -r '.model_deployment_name.value' "$output_json")" >/dev/null
    azd env set EDA_FOUNDRY_MODEL_DEPLOYMENT "$(jq -r '.model_deployment_name.value' "$output_json")" >/dev/null
    azd env set PROJECT_ENDPOINT "$(jq -r '.project_endpoint.value' "$output_json")" >/dev/null
    azd env set FOUNDRY_PROJECT_ENDPOINT "$(jq -r '.project_endpoint.value' "$output_json")" >/dev/null
    azd env set EDA_FOUNDRY_PROJECT_ENDPOINT "$(jq -r '.project_endpoint.value' "$output_json")" >/dev/null
    azd env set CONTAINER_REGISTRY_ID "$(jq -r '.container_registry_id.value' "$output_json")" >/dev/null
    azd env set AZURE_CONTAINER_REGISTRY_RESOURCE_ID "$(jq -r '.container_registry_id.value' "$output_json")" >/dev/null
    azd env set CONTAINER_REGISTRY_LOGIN_SERVER "$(jq -r '.container_registry_login_server.value' "$output_json")" >/dev/null
    azd env set AZURE_CONTAINER_REGISTRY_ENDPOINT "$(jq -r '.container_registry_login_server.value' "$output_json")" >/dev/null
    azd env set COSMOS_ACCOUNT_ID "$(jq -r '.cosmos_account_id.value' "$output_json")" >/dev/null
    azd env set COSMOS_ENDPOINT "$(jq -r '.cosmos_endpoint.value' "$output_json")" >/dev/null
    azd env set EDA_COSMOS_ENDPOINT "$(jq -r '.cosmos_endpoint.value' "$output_json")" >/dev/null
    azd env set STORAGE_ACCOUNT_ID "$(jq -r '.storage_account_id.value' "$output_json")" >/dev/null
    azd env set STORAGE_BLOB_ENDPOINT "$(jq -r '.storage_blob_endpoint.value' "$output_json")" >/dev/null
    azd env set EDA_BLOB_ACCOUNT_URL "$(jq -r '.storage_blob_endpoint.value' "$output_json")" >/dev/null
    azd env set REDIS_CLUSTER_ID "$(jq -r '.redis_cluster_id.value' "$output_json")" >/dev/null
    azd env set REDIS_HOSTNAME "$(jq -r '.redis_hostname.value' "$output_json")" >/dev/null
    azd env set EDA_REDIS_HOSTNAME "$(jq -r '.redis_hostname.value' "$output_json")" >/dev/null
    azd env set REDIS_PORT "$(jq -r '.redis_port.value' "$output_json")" >/dev/null
    azd env set EDA_REDIS_PORT "$(jq -r '.redis_port.value' "$output_json")" >/dev/null
    azd env set WEB_IDENTITY_ID "$(jq -r '.web_identity_id.value' "$output_json")" >/dev/null
    azd env set WEB_IDENTITY_CLIENT_ID "$(jq -r '.web_identity_client_id.value' "$output_json")" >/dev/null
    azd env set WEB_IDENTITY_PRINCIPAL_ID "$(jq -r '.web_identity_principal_id.value' "$output_json")" >/dev/null
    azd env set WORKER_IDENTITY_ID "$(jq -r '.worker_identity_id.value' "$output_json")" >/dev/null
    azd env set WORKER_IDENTITY_CLIENT_ID "$(jq -r '.worker_identity_client_id.value' "$output_json")" >/dev/null
    azd env set WORKER_IDENTITY_PRINCIPAL_ID "$(jq -r '.worker_identity_principal_id.value' "$output_json")" >/dev/null
    azd env set SESSION_INIT_IDENTITY_ID "$(jq -r '.session_init_identity_id.value' "$output_json")" >/dev/null
    azd env set FOUNDRY_AGENT_SUBNET_ID "$(jq -r '.foundry_agent_subnet_id.value' "$output_json")" >/dev/null
    azd env set CONTAINER_APPS_ENVIRONMENT_ID "$(jq -r '.container_apps_environment_id.value' "$output_json")" >/dev/null
    azd env set API_APP_ID "$(jq -r '.api_app_id.value' "$output_json")" >/dev/null
    azd env set CLEANUP_JOB_ID "$(jq -r '.cleanup_job_id.value' "$output_json")" >/dev/null
    if [[ "$(jq -r '.fabric_vault_url.value // empty' "$output_json")" != "" && "$(jq -r '.fabric_vault_url.value // empty' "$output_json")" != "null" ]]; then
        azd env set FABRIC_KEY_VAULT_URL "$(jq -r '.fabric_vault_url.value' "$output_json")" >/dev/null
    fi
}

for command_name in az azd terraform jq git uv; do
    require_command "$command_name"
done

terraform_version="$(terraform version -json | jq -r '.terraform_version // empty')"
[[ "$terraform_version" == "1.15.8" ]] || fail "Terraform 1.15.8 is required"

TF_VAR_FILE="${TF_VAR_FILE:-$ROOT/infra/terraform/parameters/demo.tfvars}"
TF_AZD_ENV_NAME="${TF_AZD_ENV_NAME:-tf-${AZURE_ENV_NAME:-demo-sea}}"
FABRIC_ENABLED="${FABRIC_ENABLED:-false}"
FABRIC_PROVIDER="${FABRIC_PROVIDER:-}"
DOCUMENTS_ENABLED="${DOCUMENTS_ENABLED:-false}"
POWERBI_PROJECT_ENABLED="${POWERBI_PROJECT_ENABLED:-false}"
TERRAFORM_WORKER_IMAGE="${TERRAFORM_WORKER_IMAGE:-example.azurecr.io/eda-worker@sha256:0000000000000000000000000000000000000000000000000000000000000000}"

for required_name in \
    AZURE_ENV_NAME \
    AZURE_RESOURCE_GROUP \
    AZURE_SUBSCRIPTION_ID \
    AZURE_TENANT_ID \
    AZURE_LOCATION \
    AZURE_PRINCIPAL_ID \
    EDA_MODEL_PROFILE; do
    require_value "$required_name"
done
[[ "$AZURE_LOCATION" == "southeastasia" ]] || fail "AZURE_LOCATION must be southeastasia"
[[ "$EDA_MODEL_PROFILE" == "gpt-5.6-terra-medium-v1" ]] \
    || fail "Terraform release bridge is Terra-only"
TF_STATE_PATH="${TF_STATE_PATH:-$ROOT/.artifacts/terraform-state/${TF_AZD_ENV_NAME}.tfstate}"
TF_DATA_DIR="${TF_DATA_DIR:-$ROOT/.artifacts/terraform-data/${TF_AZD_ENV_NAME}}"
export TF_DATA_DIR
mkdir -p "$(dirname "$TF_STATE_PATH")" "$TF_DATA_DIR"
chmod 700 "$(dirname "$TF_STATE_PATH")" "$TF_DATA_DIR"
[[ ! -L "$TF_STATE_PATH" ]] || fail "TF_STATE_PATH must not be a symlink"

[[ "$FABRIC_ENABLED" == "true" || "$FABRIC_ENABLED" == "false" ]] || fail "FABRIC_ENABLED must be true or false"
[[ "$DOCUMENTS_ENABLED" == "true" || "$DOCUMENTS_ENABLED" == "false" ]] || fail "DOCUMENTS_ENABLED must be true or false"
[[ "$POWERBI_PROJECT_ENABLED" == "true" || "$POWERBI_PROJECT_ENABLED" == "false" ]] || fail "POWERBI_PROJECT_ENABLED must be true or false"
if [[ "$FABRIC_ENABLED" == "true" ]]; then
    [[ "$FABRIC_PROVIDER" == "ontology" ]] || fail "Terraform mode requires FABRIC_PROVIDER=ontology when Fabric is enabled"
fi

run_step azd env new "$TF_AZD_ENV_NAME" >/dev/null 2>&1 || true
run_step azd env select "$TF_AZD_ENV_NAME" >/dev/null

run_step ${ENSURE_ENTRA_APP_CMD:-"$ROOT/scripts/ensure-entra-app.sh"}
if [[ "$FABRIC_ENABLED" == "true" ]]; then
    run_step ${CONFIGURE_FABRIC_ENTRA_CMD:-"$ROOT/scripts/configure-fabric-entra.sh"} bootstrap
fi
run_step ${PREFLIGHT_MODEL_CMD:-"$ROOT/scripts/preflight-model.sh"}
if [[ "$FABRIC_ENABLED" == "true" ]]; then
    run_step ${RUN_FABRIC_PROVIDER_HOOK_CMD:-"$ROOT/scripts/run-fabric-provider-hook.sh"} preprovision
fi

runtime_var_file="${TF_RUNTIME_VAR_FILE:-}"
owns_runtime_var_file=false
if [[ -z "$runtime_var_file" ]]; then
    runtime_var_file="$(mktemp)"
    owns_runtime_var_file=true
fi
if [[ "$owns_runtime_var_file" == "true" ]]; then
    trap 'rm -f "$runtime_var_file"' EXIT HUP INT TERM
else
    [[ ! -L "$runtime_var_file" ]] || fail "TF_RUNTIME_VAR_FILE must not be a symlink"
    mkdir -p "$(dirname "$runtime_var_file")"
fi
write_runtime_variables "$runtime_var_file"
terraform_common_args=(-var-file="$TF_VAR_FILE" -var-file="$runtime_var_file")

run_step terraform -chdir="$ROOT/infra/terraform" init -backend=false
terraform_apply "${terraform_common_args[@]}" -var deploy_image_dependent_resources=false -var worker_image="$TERRAFORM_WORKER_IMAGE"

foundation_outputs="$(mktemp)"
terraform -chdir="$ROOT/infra/terraform" output -state="$TF_STATE_PATH" -json > "$foundation_outputs"
map_terraform_outputs_to_azd "$foundation_outputs"
rm -f "$foundation_outputs"

[[ -n "$(azd_require_value CONTAINER_REGISTRY_LOGIN_SERVER)" ]]
run_step ${BUILD_WORKER_IMAGE_CMD:-"$ROOT/scripts/build-worker-image.sh"}
run_step ${BUILD_SANDBOX_IMAGE_CMD:-"$ROOT/scripts/build-sandbox-image.sh"}
worker_image="$(azd_require_value EDA_WORKER_IMAGE)"
sandbox_image="$(azd_require_value EDA_SANDBOX_IMAGE)"
[[ "$worker_image" =~ @sha256:[0-9a-f]{64}$ ]] || fail "worker image must be digest pinned before image-dependent Terraform apply"
[[ "$sandbox_image" =~ @sha256:[0-9a-f]{64}$ ]] || fail "sandbox image must be digest pinned before image-dependent Terraform apply"

terraform_apply "${terraform_common_args[@]}" -var deploy_image_dependent_resources=true -var sandbox_image="$sandbox_image" -var worker_image="$worker_image"

final_outputs="$(mktemp)"
terraform -chdir="$ROOT/infra/terraform" output -state="$TF_STATE_PATH" -json > "$final_outputs"
map_terraform_outputs_to_azd "$final_outputs"
sandbox_group_id="$(jq -er '.sandbox_group_id.value | strings' "$final_outputs")" \
    || fail "final Terraform output is missing the Sandbox group ID"
case "$sandbox_group_id" in
    /subscriptions/*/resourceGroups/*/providers/Microsoft.App/sandboxGroups/*) ;;
    *) fail "Terraform returned an invalid Sandbox group ID" ;;
esac
sandbox_group_name="${sandbox_group_id##*/}"
session_init_identity_id="$(jq -er '.session_init_identity_id.value | strings' "$final_outputs")" \
    || fail "final Terraform output is missing the Sandbox image identity"
rm -f "$final_outputs"

sandbox_digest="${sandbox_image##*@}"
disk_image_name="eda-runtime-${sandbox_digest#sha256:}"
if [[ -n "${CREATE_SANDBOX_DISK_IMAGE_CMD:-}" ]]; then
    sandbox_disk_image_id="$("$CREATE_SANDBOX_DISK_IMAGE_CMD" \
        --subscription-id "$AZURE_SUBSCRIPTION_ID" \
        --resource-group "$AZURE_RESOURCE_GROUP" \
        --sandbox-group "$sandbox_group_name" \
        --region "$AZURE_LOCATION" \
        --base-image "$sandbox_image" \
        --name "$disk_image_name" \
        --managed-identity-resource-id "$session_init_identity_id")"
else
    sandbox_disk_image_id="$(uv run --package eda-worker python "$ROOT/scripts/create-sandbox-disk-image.py" \
        --subscription-id "$AZURE_SUBSCRIPTION_ID" \
        --resource-group "$AZURE_RESOURCE_GROUP" \
        --sandbox-group "$sandbox_group_name" \
        --region "$AZURE_LOCATION" \
        --base-image "$sandbox_image" \
        --name "$disk_image_name" \
        --managed-identity-resource-id "$session_init_identity_id")"
fi
[[ -n "$sandbox_disk_image_id" ]] || fail "Sandbox disk image creation returned no ID"
azd env set EDA_SANDBOX_SUBSCRIPTION_ID "$AZURE_SUBSCRIPTION_ID" >/dev/null
azd env set EDA_SANDBOX_RESOURCE_GROUP "$AZURE_RESOURCE_GROUP" >/dev/null
azd env set EDA_SANDBOX_GROUP "$sandbox_group_name" >/dev/null
azd env set EDA_SANDBOX_REGION "$AZURE_LOCATION" >/dev/null
azd env set EDA_SANDBOX_DISK_IMAGE_ID "$sandbox_disk_image_id" >/dev/null

run_step ${CONFIGURE_ENTRA_FEDERATION_CMD:-"$ROOT/scripts/configure-entra-federation.sh"}
if [[ "$FABRIC_ENABLED" == "true" ]]; then
    run_step ${CONFIGURE_FABRIC_ENTRA_CMD:-"$ROOT/scripts/configure-fabric-entra.sh"} finalize
fi

run_step azd deploy --all
deployed_api_image="$(azd_require_value EDA_API_IMAGE)"
deployed_worker_image="$(azd_require_value EDA_WORKER_IMAGE)"
[[ "$deployed_api_image" =~ @sha256:[0-9a-f]{64}$ ]] || fail "API image must be digest pinned after azd deploy"
[[ "$deployed_worker_image" =~ @sha256:[0-9a-f]{64}$ ]] || fail "worker image must be digest pinned after azd deploy"

if [[ "$DOCUMENTS_ENABLED" == "true" ]]; then
    run_step ${RUN_DOCUMENT_ACCEPTANCE_CMD:-"$ROOT/scripts/run-document-acceptance.sh"}
fi