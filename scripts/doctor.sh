#!/bin/sh
set -eu

NONINTERACTIVE="${EDA_DOCTOR_NONINTERACTIVE:-1}"

fail() {
    printf '%s\n' "FAIL: $1" >&2
    exit 1
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || fail "required command is unavailable: $1"
}

require_value() {
    variable_name="$1"
    eval "variable_value=\${$variable_name:-}"
    [ -n "$variable_value" ] || fail "$variable_name must be set before provisioning"
}

require_minimum_version() {
    actual="$1"
    minimum="$2"
    awk -v actual="$actual" -v minimum="$minimum" '
        BEGIN {
            split(actual, a, "."); split(minimum, b, ".")
            for (part = 1; part <= 3; part++) {
                if ((a[part] + 0) > (b[part] + 0)) exit 0
                if ((a[part] + 0) < (b[part] + 0)) exit 1
            }
            exit 0
        }
    ' || fail "version $actual is below required minimum $minimum"
}

require_command "az"
require_command "azd"
require_command "docker"
require_command "jq"
require_command "uv"
require_command "node"
require_command "npm"

[ "$NONINTERACTIVE" = "1" ] || fail 'EDA_DOCTOR_NONINTERACTIVE must be 1; this preflight never prompts'
require_minimum_version "$(az version --query '"azure-cli"' --output tsv --only-show-errors)" '2.80.0'
require_minimum_version "$(azd version | sed -n 's/.*azd version \([0-9][0-9.]*\).*/\1/p' | head -n 1)" '1.31.1'
require_minimum_version "$(node --version | sed 's/^v//')" '20.0.0'
require_minimum_version "$(npm --version)" '10.0.0'
az bicep version >/dev/null 2>&1 || fail 'Azure Bicep is required'

for required_variable in \
    AZURE_SUBSCRIPTION_ID \
    AZURE_TENANT_ID \
    AZURE_LOCATION \
    AZURE_MONTHLY_BUDGET_AMOUNT \
    EDA_MODEL_PROFILE \
    EDA_DEFENDER_CONFIRMED \
    EDA_SANDBOXES_PREVIEW_CONFIRMED \
    EDA_REDIS_SKU_CONFIRMED; do
    require_value "$required_variable"
done

case "$AZURE_MONTHLY_BUDGET_AMOUNT" in
    *[!0-9]* | '') fail 'AZURE_MONTHLY_BUDGET_AMOUNT must be a positive whole number' ;;
esac
[ "$AZURE_MONTHLY_BUDGET_AMOUNT" -gt 0 ] || fail 'AZURE_MONTHLY_BUDGET_AMOUNT must be greater than zero'

for acknowledgement in \
    EDA_DEFENDER_CONFIRMED \
    EDA_SANDBOXES_PREVIEW_CONFIRMED \
    EDA_REDIS_SKU_CONFIRMED; do
    eval "acknowledgement_value=\${$acknowledgement}"
    [ "$acknowledgement_value" = "true" ] || fail "$acknowledgement must be explicitly true"
done

[ "$EDA_MODEL_PROFILE" = "gpt-5.6-terra-medium-v1" ] \
    || fail 'EDA_MODEL_PROFILE must be gpt-5.6-terra-medium-v1 for this deployment'

account_json="$(az account show --only-show-errors --output json)" || fail 'unable to read the active Azure account'
active_subscription_id="$(printf '%s' "$account_json" | jq -r '.id // empty')"
active_tenant_id="$(printf '%s' "$account_json" | jq -r '.tenantId // empty')"
[ "$active_subscription_id" = "$AZURE_SUBSCRIPTION_ID" ] || fail 'active subscription does not match AZURE_SUBSCRIPTION_ID'
[ "$active_tenant_id" = "$AZURE_TENANT_ID" ] || fail 'active tenant does not match AZURE_TENANT_ID'

az role assignment list --assignee "$(az ad signed-in-user show --query id --output tsv --only-show-errors)" \
    --scope "/subscriptions/$AZURE_SUBSCRIPTION_ID" --only-show-errors >/dev/null \
    || fail 'the deployer must be able to inspect subscription role assignments'

for provider in Microsoft.App Microsoft.Cache Microsoft.CognitiveServices Microsoft.Consumption Microsoft.DocumentDB Microsoft.Insights Microsoft.OperationalInsights Microsoft.Storage; do
    state="$(az provider show --namespace "$provider" --query registrationState --output tsv --only-show-errors)" || fail "unable to inspect provider $provider"
    [ "$state" = "Registered" ] || fail "provider $provider is not registered"
done

for resource_type in \
    'Microsoft.App/containerApps' \
    'Microsoft.App/sandboxGroups' \
    'Microsoft.Cache/redisEnterprise' \
    'Microsoft.CognitiveServices/accounts' \
    'Microsoft.DocumentDB/databaseAccounts' \
    'Microsoft.Storage/storageAccounts'; do
    provider_name="${resource_type%%/*}"
    type_name="${resource_type#*/}"
    az provider show --namespace "$provider_name" --query "resourceTypes[?resourceType=='$type_name'].locations[]" \
        --output tsv --only-show-errors | tr '[:upper:]' '[:lower:]' | sed 's/[[:space:]]//g' \
        | grep -Fx "$(printf '%s' "$AZURE_LOCATION" | tr '[:upper:]' '[:lower:]' | sed 's/[[:space:]]//g')" >/dev/null \
        || fail "$resource_type is unavailable in AZURE_LOCATION"
done

if [ -n "${EDA_ENTRA_APP_CLIENT_ID:-}" ]; then
    az ad app show --id "$EDA_ENTRA_APP_CLIENT_ID" --only-show-errors >/dev/null \
        || fail 'EDA_ENTRA_APP_CLIENT_ID cannot be reused in the active tenant'
else
    az ad app list --display-name "eda-${AZURE_ENV_NAME:-}" --query '[].appId' --output tsv --only-show-errors >/dev/null \
        || fail 'the deployer cannot inspect Entra application registrations'
fi

printf '%s\n' "Doctor passed for subscription $AZURE_SUBSCRIPTION_ID in $AZURE_LOCATION"