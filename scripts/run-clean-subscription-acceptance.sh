#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT="${EDA_RELEASE_MANIFEST:-$ROOT/.artifacts/release-acceptance.json}"
TERRA_PROFILE="gpt-5.6-terra-medium-v1"
CURRENT_ENV=""
CURRENT_RESOURCE_GROUP=""
CURRENT_SUBSCRIPTION=""
CURRENT_IAC=""
CURRENT_OWNED=false

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

require_true() {
    local name="$1"
    [[ "${!name:-}" == "true" ]] || fail "$name must be explicitly true"
}

require_boolean() {
    local name="$1"
    local value="${!name:-false}"
    [[ "$value" == "true" || "$value" == "false" ]] || fail "$name must be true or false"
}

require_private_directory() {
    local path="$1"
    [[ -d "$path" && ! -L "$path" ]] || fail "isolated CLI context does not exist: $path"
    [[ "$(stat -c '%a' "$path")" == "700" ]] || fail "isolated CLI context must have mode 0700: $path"
}

require_mode_600_file() {
    local path="$1"
    [[ -f "$path" && ! -L "$path" ]] || fail "evidence must be a regular file: $path"
    [[ "$(stat -c '%a' "$path")" == "600" ]] || fail "evidence must have mode 0600: $path"
}

product_az() {
    AZURE_CONFIG_DIR="$PRODUCT_AZURE_CONFIG_DIR" az "$@"
}

fabric_az() {
    AZURE_CONFIG_DIR="$FABRIC_AZURE_CONFIG_DIR" az "$@"
}

invoke_step() {
    local override_name="$1"
    local default_function="$2"
    local override_path="${!override_name:-}"
    if [[ -n "$override_path" ]]; then
        [[ -x "$override_path" ]] || fail "$override_name must name an executable file"
        "$override_path"
        return
    fi
    "$default_function"
}

azd_set() {
    azd env set "$1" "$2" --environment "$CURRENT_ENV" >/dev/null
}

validate_uuid() {
    [[ "$1" =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$ ]]
}

validate_resource_group_name() {
    [[ "$1" =~ ^rg-eda-rel-[a-z0-9]{4,12}-(bicep|terraform)-sea$ ]]
}

ensure_no_existing_app() {
    local display_name="$1"
    local context="$2"
    local count
    if [[ "$context" == "fabric" ]]; then
        count="$(fabric_az ad app list --display-name "$display_name" --query "length([?displayName=='$display_name'])" --output tsv --only-show-errors)"
    else
        count="$(product_az ad app list --display-name "$display_name" --query "length([?displayName=='$display_name'])" --output tsv --only-show-errors)"
    fi
    [[ "$count" == "0" ]] || fail "disposable application name already exists: $display_name"
}

prepare_cell() {
    product_az account set --subscription "$CURRENT_SUBSCRIPTION" --only-show-errors
    local account_subscription account_tenant group_exists
    account_subscription="$(product_az account show --query id --output tsv --only-show-errors)"
    account_tenant="$(product_az account show --query tenantId --output tsv --only-show-errors)"
    [[ "$account_subscription" == "$CURRENT_SUBSCRIPTION" ]] || fail "active release subscription does not match"
    [[ "$account_tenant" == "$AZURE_TENANT_ID" ]] || fail "active release tenant does not match"
    group_exists="$(product_az group exists --name "$CURRENT_RESOURCE_GROUP" --subscription "$CURRENT_SUBSCRIPTION" --output tsv)"
    [[ "$group_exists" == "false" ]] || fail "release resource group already exists: $CURRENT_RESOURCE_GROUP"
    ensure_no_existing_app "eda-$CURRENT_ENV" product
    if [[ "$FABRIC_ENABLED" == "true" ]]; then
        ensure_no_existing_app "eda-fabric-$CURRENT_ENV" fabric
    fi
    [[ ! -e "$ROOT/.azure/$CURRENT_ENV" ]] || fail "azd release environment already exists: $CURRENT_ENV"

    azd env new "$CURRENT_ENV" --no-prompt >/dev/null
    CURRENT_OWNED=true
    azd env select "$CURRENT_ENV" --no-prompt >/dev/null
    azd_set AZURE_ENV_NAME "$CURRENT_ENV"
    azd_set AZURE_SUBSCRIPTION_ID "$CURRENT_SUBSCRIPTION"
    azd_set AZURE_TENANT_ID "$AZURE_TENANT_ID"
    azd_set AZURE_LOCATION "$AZURE_LOCATION"
    azd_set AZURE_RESOURCE_GROUP "$CURRENT_RESOURCE_GROUP"
    azd_set AZURE_PRINCIPAL_ID "$AZURE_PRINCIPAL_ID"
    azd_set AZURE_MONTHLY_BUDGET_AMOUNT "$AZURE_MONTHLY_BUDGET_AMOUNT"
    azd_set EDA_MODEL_PROFILE "$TERRA_PROFILE"
    azd_set EDA_DEFENDER_CONFIRMED "true"
    azd_set EDA_SANDBOXES_PREVIEW_CONFIRMED "true"
    azd_set EDA_REDIS_SKU_CONFIRMED "true"
    azd_set FABRIC_ENABLED "$FABRIC_ENABLED"
    azd_set FABRIC_PROVIDER "$FABRIC_PROVIDER"
    azd_set FABRIC_TENANT_ID "${FABRIC_TENANT_ID:-}"
    azd_set FABRIC_SEMANTIC_MODELS_JSON "${FABRIC_SEMANTIC_MODELS_JSON:-{}}"
    azd_set FABRIC_ONTOLOGIES_JSON "${FABRIC_ONTOLOGIES_JSON:-{}}"
    azd_set FABRIC_ACCEPTANCE_PRINCIPAL_ID "$EDA_ACCEPTANCE_PRINCIPAL_ID"
    azd_set DOCUMENTS_ENABLED "$DOCUMENTS_ENABLED"
    azd_set POWERBI_PROJECT_ENABLED "false"
}

deploy_bicep() {
    azd up --environment "$CURRENT_ENV" --no-prompt
}

deploy_terraform() {
    export TF_AZD_ENV_NAME="$CURRENT_ENV"
    export TF_RUNTIME_VAR_FILE="$ROOT/.artifacts/terraform-state/$CURRENT_ENV.runtime.tfvars.json"
    export TF_STATE_PATH="$ROOT/.artifacts/terraform-state/$CURRENT_ENV.tfstate"
    export TF_DATA_DIR="$ROOT/.artifacts/terraform-data/$CURRENT_ENV"
    "$ROOT/scripts/deploy-terraform-environment.sh"
}

selected_eval_results() {
    local path
    if [[ "$CURRENT_IAC" == "bicep" ]]; then
        path="${EDA_RELEASE_BICEP_EVAL_RESULTS:-}"
    else
        path="${EDA_RELEASE_TERRAFORM_EVAL_RESULTS:-}"
    fi
    [[ -n "$path" ]] || fail "fresh $CURRENT_IAC eval results are required"
    require_mode_600_file "$path"
    printf '%s\n' "$path"
}

azd_first_value() {
    local name value
    for name in "$@"; do
        value="$(azd env get-value "$name" --environment "$CURRENT_ENV" 2>/dev/null || true)"
        if [[ -n "$value" ]]; then
            printf '%s\n' "$value"
            return
        fi
    done
    fail "none of the required azd values are set: $*"
}

run_release_gates() {
    export EDA_APP_URL="$(azd env get-value API_URL --environment "$CURRENT_ENV")"
    export EDA_API_IMAGE="$(azd env get-value EDA_API_IMAGE --environment "$CURRENT_ENV")"
    export EDA_WORKER_IMAGE="$(azd env get-value EDA_WORKER_IMAGE --environment "$CURRENT_ENV")"
    export EDA_SANDBOX_IMAGE="$(azd env get-value EDA_SANDBOX_IMAGE --environment "$CURRENT_ENV")"
    [[ "$EDA_API_IMAGE" =~ @sha256:[0-9a-f]{64}$ ]] || fail "deployed API image is not digest pinned"
    [[ "$EDA_WORKER_IMAGE" =~ @sha256:[0-9a-f]{64}$ ]] || fail "deployed worker image is not digest pinned"
    [[ "$EDA_SANDBOX_IMAGE" =~ @sha256:[0-9a-f]{64}$ ]] || fail "deployed sandbox image is not digest pinned"

    make deployed-core-gate
    if [[ "$FABRIC_ENABLED" == "true" ]]; then
        if [[ "$FABRIC_PROVIDER" == "semantic_model" ]]; then
            make acceptance-fabric
        else
            make acceptance-fabric-ontology
        fi
    fi
    make acceptance-documents
    make supply-chain-gate
    export EDA_EVAL_RESULTS="$(selected_eval_results)"
    make eval-gate
}

delete_owned_resource_group() {
    local exists
    exists="$(product_az group exists --name "$CURRENT_RESOURCE_GROUP" --subscription "$CURRENT_SUBSCRIPTION" --output tsv)"
    if [[ "$exists" == "true" ]]; then
        product_az group delete \
            --name "$CURRENT_RESOURCE_GROUP" \
            --subscription "$CURRENT_SUBSCRIPTION" \
            --yes \
            --only-show-errors
    elif [[ "$exists" != "false" ]]; then
        return 1
    fi
}

teardown_bicep() {
    local status=0
    azd down --environment "$CURRENT_ENV" --purge --force --no-prompt || status=$?
    delete_owned_resource_group || status=$?
    return "$status"
}

teardown_terraform() {
    local status=0
    local state_path="$ROOT/.artifacts/terraform-state/$CURRENT_ENV.tfstate"
    local runtime_vars="$ROOT/.artifacts/terraform-state/$CURRENT_ENV.runtime.tfvars.json"
    local data_dir="$ROOT/.artifacts/terraform-data/$CURRENT_ENV"
    if [[ -f "$state_path" && -f "$runtime_vars" ]]; then
        TF_DATA_DIR="$data_dir" terraform -chdir="$ROOT/infra/terraform" destroy \
            -auto-approve \
            -state="$state_path" \
            -var-file="$TF_VAR_FILE" \
            -var-file="$runtime_vars" || status=$?
    fi
    delete_owned_resource_group || status=$?
    return "$status"
}

cleanup_disposable_apps() {
    local app_id
    while IFS= read -r app_id; do
        [[ -z "$app_id" ]] || product_az ad app delete --id "$app_id" --only-show-errors
    done < <(product_az ad app list \
        --display-name "eda-$CURRENT_ENV" \
        --query "[?displayName=='eda-$CURRENT_ENV'].appId" \
        --output tsv \
        --only-show-errors)
    if [[ "$FABRIC_ENABLED" == "true" ]]; then
        while IFS= read -r app_id; do
            [[ -z "$app_id" ]] || fabric_az ad app delete --id "$app_id" --only-show-errors
        done < <(fabric_az ad app list \
            --display-name "eda-fabric-$CURRENT_ENV" \
            --query "[?displayName=='eda-fabric-$CURRENT_ENV'].appId" \
            --output tsv \
            --only-show-errors)
    fi
}

verify_zero_resources() {
    local group_exists remaining
    group_exists="$(product_az group exists --name "$CURRENT_RESOURCE_GROUP" --subscription "$CURRENT_SUBSCRIPTION" --output tsv)"
    [[ "$group_exists" == "false" ]] || fail "release resource group still exists after teardown"
    remaining="$(product_az graph query \
        --subscriptions "$CURRENT_SUBSCRIPTION" \
        --graph-query "Resources | where subscriptionId =~ '$CURRENT_SUBSCRIPTION' | where resourceGroup =~ '$CURRENT_RESOURCE_GROUP' | project id" \
        --first 1000 \
        --query 'length(data)' \
        --output tsv \
        --only-show-errors)"
    [[ "$remaining" == "0" ]] || fail "Resource Graph still reports resources after teardown"
}

remove_azd_environment() {
    azd env remove "$CURRENT_ENV" --force --no-prompt >/dev/null
    [[ ! -e "$ROOT/.azure/$CURRENT_ENV" ]] || fail "azd environment directory remains after removal"
    rm -rf \
        "$ROOT/.artifacts/terraform-data/$CURRENT_ENV" \
        "$ROOT/.artifacts/terraform-state/$CURRENT_ENV.tfstate" \
        "$ROOT/.artifacts/terraform-state/$CURRENT_ENV.tfstate.backup" \
        "$ROOT/.artifacts/terraform-state/$CURRENT_ENV.runtime.tfvars.json"
}

teardown_current_cell() {
    [[ "$CURRENT_OWNED" == "true" ]] || return 0
    local status=0
    if [[ "$CURRENT_IAC" == "bicep" ]]; then
        invoke_step RELEASE_BICEP_TEARDOWN_CMD teardown_bicep || status=$?
    else
        invoke_step RELEASE_TERRAFORM_TEARDOWN_CMD teardown_terraform || status=$?
    fi
    invoke_step RELEASE_IDENTITY_CLEANUP_CMD cleanup_disposable_apps || status=$?
    invoke_step RELEASE_ZERO_CHECK_CMD verify_zero_resources || status=$?
    remove_azd_environment || status=$?
    CURRENT_OWNED=false
    return "$status"
}

run_cell() {
    local deploy_status=0 gate_status=0 teardown_status=0
    prepare_cell
    if [[ "$CURRENT_IAC" == "bicep" ]]; then
        invoke_step RELEASE_BICEP_DEPLOY_CMD deploy_bicep || deploy_status=$?
    else
        invoke_step RELEASE_TERRAFORM_DEPLOY_CMD deploy_terraform || deploy_status=$?
    fi
    if [[ "$deploy_status" == "0" ]]; then
        invoke_step RELEASE_GATE_CMD run_release_gates || gate_status=$?
    fi
    teardown_current_cell || teardown_status=$?
    if [[ "$deploy_status" != "0" || "$gate_status" != "0" || "$teardown_status" != "0" ]]; then
        printf '%s\n' "FAIL: release cell $CURRENT_IAC failed deploy=$deploy_status gate=$gate_status teardown=$teardown_status" >&2
        return 1
    fi
    return 0
}

emergency_cleanup() {
    local exit_status=$?
    if [[ "$CURRENT_OWNED" == "true" ]]; then
        teardown_current_cell || true
    fi
    exit "$exit_status"
}

for command_name in az azd terraform jq sha256sum stat make; do
    require_command "$command_name"
done

require_true EDA_RELEASE_DISPOSABLE_CONFIRMED
require_value EDA_RELEASE_RUN_ID
require_value EDA_RELEASE_SUBSCRIPTION_IDS
require_value AZURE_TENANT_ID
require_value AZURE_PRINCIPAL_ID
require_value EDA_ACCEPTANCE_PRINCIPAL_ID
require_value AZURE_MONTHLY_BUDGET_AMOUNT
require_value PRODUCT_AZURE_CONFIG_DIR
require_true EDA_DEFENDER_CONFIRMED
require_true EDA_SANDBOXES_PREVIEW_CONFIRMED
require_true EDA_REDIS_SKU_CONFIRMED
require_boolean FABRIC_ENABLED
require_boolean DOCUMENTS_ENABLED
require_private_directory "$PRODUCT_AZURE_CONFIG_DIR"

[[ "$EDA_RELEASE_RUN_ID" =~ ^[a-z0-9]{4,12}$ ]] || fail "EDA_RELEASE_RUN_ID must be 4-12 lowercase letters or digits"
[[ "${EDA_RELEASE_MODEL_PROFILES:-}" == "$TERRA_PROFILE" ]] || fail "release model profile must be Terra only"
[[ "${AZURE_LOCATION:-southeastasia}" == "southeastasia" ]] || fail "release location must be southeastasia"
AZURE_LOCATION="southeastasia"
export AZURE_LOCATION PRODUCT_AZURE_CONFIG_DIR
export AZURE_CONFIG_DIR="$PRODUCT_AZURE_CONFIG_DIR"
[[ -z "${EDA_ENTRA_APP_CLIENT_ID:-}" ]] || fail "clean acceptance must create a disposable product Entra app"
[[ -z "${FABRIC_CLIENT_ID:-}" ]] || fail "clean acceptance must create a disposable Fabric Entra app"
validate_uuid "$AZURE_TENANT_ID" || fail "AZURE_TENANT_ID must be a UUID"
validate_uuid "$AZURE_PRINCIPAL_ID" || fail "AZURE_PRINCIPAL_ID must be a UUID"
validate_uuid "$EDA_ACCEPTANCE_PRINCIPAL_ID" || fail "EDA_ACCEPTANCE_PRINCIPAL_ID must be a UUID"

FABRIC_ENABLED="${FABRIC_ENABLED:-false}"
FABRIC_PROVIDER="${FABRIC_PROVIDER:-}"
DOCUMENTS_ENABLED="${DOCUMENTS_ENABLED:-false}"
export FABRIC_ENABLED FABRIC_PROVIDER DOCUMENTS_ENABLED
export FABRIC_ACCEPTANCE_ENABLED=true
if [[ "$FABRIC_ENABLED" == "true" ]]; then
    require_value FABRIC_AZURE_CONFIG_DIR
    require_value FABRIC_TENANT_ID
    require_private_directory "$FABRIC_AZURE_CONFIG_DIR"
    validate_uuid "$FABRIC_TENANT_ID" || fail "FABRIC_TENANT_ID must be a UUID"
    [[ "$FABRIC_TENANT_ID" != "$AZURE_TENANT_ID" ]] || fail "Fabric acceptance requires a distinct tenant"
    [[ "$FABRIC_PROVIDER" == "semantic_model" || "$FABRIC_PROVIDER" == "ontology" ]] \
        || fail "enabled Fabric requires a known provider"
    if [[ "$FABRIC_PROVIDER" == "semantic_model" ]]; then
        [[ -n "${FABRIC_SEMANTIC_MODELS_JSON:-}" && "${FABRIC_SEMANTIC_MODELS_JSON}" != "{}" ]] \
            || fail "semantic-model release requires its catalog"
    else
        [[ -n "${FABRIC_ONTOLOGIES_JSON:-}" && "${FABRIC_ONTOLOGIES_JSON}" != "{}" ]] \
            || fail "ontology release requires its catalog"
    fi
    export FABRIC_AZURE_CONFIG_DIR FABRIC_TENANT_ID
elif [[ -n "$FABRIC_PROVIDER" ]]; then
    fail "disabled Fabric must not select a provider"
fi
if [[ "$DOCUMENTS_ENABLED" == "true" ]]; then
    require_value EDA_DOCUMENT_TERMS_ACCEPTED
fi
terraform_version="$(terraform version -json | jq -r '.terraform_version // empty')"
[[ "$terraform_version" == "1.15.8" ]] || fail "Terraform 1.15.8 is required"

IFS=',' read -r -a release_subscriptions <<<"$EDA_RELEASE_SUBSCRIPTION_IDS"
[[ "${#release_subscriptions[@]}" -ge 1 && "${#release_subscriptions[@]}" -le 2 ]] \
    || fail "EDA_RELEASE_SUBSCRIPTION_IDS must contain one or two subscription UUIDs"
for subscription in "${release_subscriptions[@]}"; do
    validate_uuid "$subscription" || fail "release subscription IDs must be UUIDs without whitespace"
done
if [[ "${#release_subscriptions[@]}" == "2" && "${release_subscriptions[0]}" == "${release_subscriptions[1]}" ]]; then
    fail "two supplied release subscriptions must be distinct"
fi

mkdir -p "$(dirname "$OUTPUT")" "$ROOT/.artifacts/terraform-state" "$ROOT/.artifacts/terraform-data"
rm -f "$OUTPUT"
cells_file="$(mktemp)"
chmod 600 "$cells_file"
trap 'rm -f "$cells_file"' EXIT
trap emergency_cleanup HUP INT TERM

overall_status=0
for index in 0 1; do
    if [[ "$index" == "0" ]]; then
        CURRENT_IAC="bicep"
        suffix="bicep"
    else
        CURRENT_IAC="terraform"
        suffix="terraform"
    fi
    if [[ "${#release_subscriptions[@]}" == "1" ]]; then
        CURRENT_SUBSCRIPTION="${release_subscriptions[0]}"
    else
        CURRENT_SUBSCRIPTION="${release_subscriptions[$index]}"
    fi
    CURRENT_ENV="rel-${EDA_RELEASE_RUN_ID}-${suffix}"
    CURRENT_RESOURCE_GROUP="rg-eda-rel-${EDA_RELEASE_RUN_ID}-${suffix}-sea"
    CURRENT_OWNED=false
    validate_resource_group_name "$CURRENT_RESOURCE_GROUP" || fail "generated release resource group name is invalid"
    export CURRENT_IAC CURRENT_SUBSCRIPTION CURRENT_ENV CURRENT_RESOURCE_GROUP
    export EDA_RELEASE_IAC_PATH="$CURRENT_IAC"
    export AZURE_SUBSCRIPTION_ID="$CURRENT_SUBSCRIPTION"
    export AZURE_ENV_NAME="$CURRENT_ENV"
    export AZURE_RESOURCE_GROUP="$CURRENT_RESOURCE_GROUP"
    export EDA_MODEL_PROFILE="$TERRA_PROFILE"
    export FABRIC_ACCEPTANCE_PRINCIPAL_ID="$EDA_ACCEPTANCE_PRINCIPAL_ID"
    export TF_VAR_FILE="${TF_VAR_FILE:-$ROOT/infra/terraform/parameters/demo.tfvars}"

    if run_cell; then
        subscription_sha256="$(printf '%s' "$CURRENT_SUBSCRIPTION" | sha256sum | cut -d' ' -f1)"
        resource_group_sha256="$(printf '%s' "$CURRENT_RESOURCE_GROUP" | sha256sum | cut -d' ' -f1)"
        jq -cn \
            --arg iac "$CURRENT_IAC" \
            --arg profile "$TERRA_PROFILE" \
            --arg subscriptionSha256 "$subscription_sha256" \
            --arg resourceGroupSha256 "$resource_group_sha256" \
            '{iac: $iac, modelProfile: $profile, subscriptionSha256: $subscriptionSha256, resourceGroupSha256: $resourceGroupSha256, status: "passed", teardown: "verified_zero"}' \
            >>"$cells_file"
    else
        overall_status=1
    fi
done

if [[ "$overall_status" != "0" ]]; then
    rm -f "$OUTPUT"
    fail "clean-subscription release matrix failed"
fi

cells="$(jq -s '.' "$cells_file")"
temporary_output="$(mktemp "$(dirname "$OUTPUT")/.release-acceptance.XXXXXX")"
chmod 600 "$temporary_output"
jq -n \
    --arg runId "$EDA_RELEASE_RUN_ID" \
    --arg modelProfile "$TERRA_PROFILE" \
    --argjson cells "$cells" \
    '{schemaVersion: 1, state: "passed", runId: $runId, modelProfile: $modelProfile, cells: $cells}' \
    >"$temporary_output"
mv "$temporary_output" "$OUTPUT"
printf '%s\n' "PASS: Terra clean-subscription matrix completed for Bicep and Terraform"
