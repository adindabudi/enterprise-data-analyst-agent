#!/bin/sh
set -eu

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
    [ -n "$variable_value" ] || fail "$variable_name must be set"
}

require_command az
require_command jq

for variable_name in \
    AZURE_SUBSCRIPTION_ID \
    AZURE_TENANT_ID \
    AZURE_LOCATION \
    EDA_MODEL_PROFILE \
    FOUNDRY_MODEL_CAPACITY; do
    require_value "$variable_name"
done

[ "$EDA_MODEL_PROFILE" = 'gpt-5.6-terra-medium-v1' ] \
    || fail 'EDA_MODEL_PROFILE must be gpt-5.6-terra-medium-v1'
case "$FOUNDRY_MODEL_CAPACITY" in
    *[!0-9]* | '') fail 'FOUNDRY_MODEL_CAPACITY must be a positive integer' ;;
esac
[ "$FOUNDRY_MODEL_CAPACITY" -gt 0 ] || fail 'FOUNDRY_MODEL_CAPACITY must be positive'

account_json="$(az account show --output json --only-show-errors)" || fail 'unable to read the active Azure account'
active_subscription_id="$(printf '%s' "$account_json" | jq -r '.id // empty')"
active_tenant_id="$(printf '%s' "$account_json" | jq -r '.tenantId // empty')"
[ "$active_subscription_id" = "$AZURE_SUBSCRIPTION_ID" ] \
    || fail 'active subscription does not match AZURE_SUBSCRIPTION_ID'
[ "$active_tenant_id" = "$AZURE_TENANT_ID" ] \
    || fail 'active tenant does not match AZURE_TENANT_ID'

catalog_file="$(mktemp)"
usage_file="$(mktemp)"
trap 'rm -f "$catalog_file" "$usage_file"' EXIT HUP INT TERM

az cognitiveservices model list --location "$AZURE_LOCATION" --output json --only-show-errors >"$catalog_file" \
    || fail 'unable to read the Foundry model catalog for AZURE_LOCATION'

catalog_filter='any(.[];
    .kind == "AIServices"
    and .model.format == "OpenAI"
    and .model.name == "gpt-5.6-terra"
    and .model.version == "2026-07-09"
    and .model.capabilities.responses == "true"
    and any(.model.skus[]?; .name == "GlobalStandard")
)'
jq -e "$catalog_filter" "$catalog_file" >/dev/null \
    || fail 'the live catalog has no exact GPT-5.6 Terra 2026-07-09 GlobalStandard Responses deployment'

usage_name="$(jq -r '
    .[]
    | select(
        .kind == "AIServices"
        and .model.format == "OpenAI"
        and .model.name == "gpt-5.6-terra"
        and .model.version == "2026-07-09"
    )
    | .model.skus[]?
    | select(.name == "GlobalStandard")
    | .usageName
' "$catalog_file" | head -n 1)"
[ -n "$usage_name" ] || fail 'the live catalog did not return Terra quota usage metadata'

az cognitiveservices usage list --location "$AZURE_LOCATION" --output json --only-show-errors >"$usage_file" \
    || fail 'unable to read Cognitive Services quota for AZURE_LOCATION'
available_capacity="$(jq -r --arg usage_name "$usage_name" '
    [.[] | select(.name.value == $usage_name) | ((.limit // 0) - (.currentValue // 0))] | max // -1
' "$usage_file")"
awk -v available="$available_capacity" -v requested="$FOUNDRY_MODEL_CAPACITY" \
    'BEGIN { exit !(available >= requested) }' \
    || fail 'GPT-5.6 Terra quota headroom is below FOUNDRY_MODEL_CAPACITY'

principal_id="$(az ad signed-in-user show --query id --output tsv --only-show-errors)" \
    || fail 'unable to resolve the signed-in deployer object ID'
role_names="$(az role assignment list --assignee "$principal_id" --all --query '[].roleDefinitionName' --output tsv --only-show-errors)" \
    || fail 'unable to inspect deployer role assignments'
printf '%s\n' "$role_names" | grep -Fx 'Owner' >/dev/null \
    || fail 'the deployer requires Owner to deploy the model and assign the worker role'

printf '%s\n' "PASS: GPT-5.6 Terra 2026-07-09 GlobalStandard Responses catalog, quota, and deployer authorization verified in $AZURE_LOCATION."
