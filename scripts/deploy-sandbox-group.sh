#!/bin/sh
set -eu

fail() {
    printf '%s\n' "FAIL: $1" >&2
    exit 1
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || fail "required command is unavailable: $1"
}

for command_name in az azd jq uv; do
    require_command "$command_name"
done

azd_values="$(azd env get-values)" || fail "unable to read azd environment values"

azd_value() {
    variable_name="$1"
    variable_value="$(printf '%s\n' "$azd_values" | sed -n "s/^${variable_name}=//p" | head -n 1)"
    case "$variable_value" in
        \"*\") variable_value="${variable_value#\"}"; variable_value="${variable_value%\"}" ;;
    esac
    [ -n "$variable_value" ] || fail "azd output ${variable_name} must be set"
    printf '%s\n' "$variable_value"
}

environment_name="$(azd_value AZURE_ENV_NAME)"
subscription_id="$(azd_value AZURE_SUBSCRIPTION_ID)"
resource_group="$(azd_value AZURE_RESOURCE_GROUP)"
location="$(azd_value AZURE_LOCATION)"
sandbox_subnet_id="$(azd_value SANDBOX_SUBNET_ID)"
provisioning_principal_id="$(azd_value AZURE_PRINCIPAL_ID)"
session_init_identity_id="$(azd_value SESSION_INIT_IDENTITY_ID)"
api_app_id="$(azd_value API_APP_ID)"
web_identity_principal_id="$(azd_value WEB_IDENTITY_PRINCIPAL_ID)"

case "$environment_name" in
    *[!a-z0-9-]* | '') fail "AZURE_ENV_NAME must contain only lowercase letters, digits, and hyphens" ;;
esac
case "$subscription_id" in
    ????????-????-????-????-????????????) ;;
    *) fail "AZURE_SUBSCRIPTION_ID is invalid" ;;
esac
case "$sandbox_subnet_id" in
    /subscriptions/*/resourceGroups/*/providers/Microsoft.Network/virtualNetworks/*/subnets/*) ;;
    *) fail "SANDBOX_SUBNET_ID is not a subnet resource ID" ;;
esac
case "$provisioning_principal_id" in
    ????????-????-????-????-????????????) ;;
    *) fail "AZURE_PRINCIPAL_ID is not a principal ID" ;;
esac
case "$web_identity_principal_id" in
    ????????-????-????-????-????????????) ;;
    *) fail "WEB_IDENTITY_PRINCIPAL_ID is not a principal ID" ;;
esac
project_root="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
manifest_file="$project_root/.azure/$environment_name/sandbox-image.json"
[ -f "$manifest_file" ] || fail "sandbox image manifest is missing; run build-sandbox-image.sh first"
sandbox_image="$(jq -er '.image | strings' "$manifest_file")" || fail "sandbox image manifest has no image value"
sandbox_digest="$(jq -er '.digest | strings' "$manifest_file")" || fail "sandbox image manifest has no digest value"
registry_login_server="$(azd_value CONTAINER_REGISTRY_LOGIN_SERVER)"
printf '%s' "$sandbox_image" | grep -Eq '^[a-z0-9][a-z0-9.-]*/[a-z0-9][a-z0-9._/-]*@sha256:[0-9a-f]{64}$' \
    || fail "sandbox image must be an ACR image pinned by SHA-256 digest"
case "$sandbox_image" in
    "${registry_login_server}/"*) ;;
    *) fail "sandbox image must belong to the provisioned registry" ;;
esac
registry_name="${registry_login_server%%.*}"

sandbox_group_name="sbg-eda-${environment_name}"
deployment_outputs="$(az deployment group create \
    --resource-group "$resource_group" \
    --name "sandbox-group-${environment_name}" \
    --template-file "$project_root/infra/bicep/modules/sandbox-group.bicep" \
    --parameters \
        location="$location" \
        sandboxGroupName="$sandbox_group_name" \
        sandboxSubnetId="$sandbox_subnet_id" \
        provisioningPrincipalId="$provisioning_principal_id" \
        sessionInitIdentityResourceId="$session_init_identity_id" \
        webIdentityPrincipalId="$web_identity_principal_id" \
        tags="{\"azd-env-name\":\"$environment_name\",\"application\":\"enterprise-data-analyst\",\"managedBy\":\"bicep\"}" \
    --query properties.outputs \
    --output json \
    --only-show-errors)" || fail "sandbox group deployment failed"
sandbox_group_id="$(printf '%s' "$deployment_outputs" | jq -er '.sandboxGroupId.value | strings')" \
    || fail "sandbox group deployment did not return sandboxGroupId"
sandbox_data_owner_role_id="/subscriptions/${subscription_id}/providers/Microsoft.Authorization/roleDefinitions/c24cf47c-5077-412d-a19c-45202126392c"
existing_api_sandbox_owner="$(az role assignment list \
    --assignee "$web_identity_principal_id" \
    --scope "$sandbox_group_id" \
    --role "$sandbox_data_owner_role_id" \
    --query 'length(@)' \
    --output tsv \
    --only-show-errors)" || fail "unable to inspect API sandbox data-owner assignment"
if [ "$existing_api_sandbox_owner" = "0" ]; then
    az role assignment create \
        --assignee-object-id "$web_identity_principal_id" \
        --assignee-principal-type ServicePrincipal \
        --role "$sandbox_data_owner_role_id" \
        --scope "$sandbox_group_id" \
        --only-show-errors >/dev/null \
        || fail "unable to assign API sandbox data-owner role"
fi
verified_api_sandbox_owner="$(az role assignment list \
    --assignee "$web_identity_principal_id" \
    --scope "$sandbox_group_id" \
    --role "$sandbox_data_owner_role_id" \
    --query 'length(@)' \
    --output tsv \
    --only-show-errors)" || fail "unable to verify API sandbox data-owner assignment"
[ "$verified_api_sandbox_owner" != "0" ] || fail "API sandbox data-owner assignment is missing"

# Managed-identity disk imports are broken in azure-containerapps-sandbox 0.1.0b4
# (microsoft/azure-container-apps#1768).
# Stream a short-lived Entra-backed ACR token directly to the importer instead.
sandbox_disk_image_id="$(az acr login \
    --name "$registry_name" \
    --expose-token \
    --query accessToken \
    --output tsv \
    --only-show-errors \
    | uv run --package eda-worker python "$project_root/scripts/create-sandbox-disk-image.py" \
        --subscription-id "$subscription_id" \
        --resource-group "$resource_group" \
        --sandbox-group "$sandbox_group_name" \
        --region "$location" \
        --base-image "$sandbox_image" \
        --name "eda-runtime-${sandbox_digest#sha256:}" \
        --registry-token-stdin)" \
    || fail "sandbox disk image creation failed"
[ -n "$sandbox_disk_image_id" ] || fail "sandboxDiskImageId was not returned"

for variable in \
    "EDA_SANDBOX_SUBSCRIPTION_ID=$subscription_id" \
    "EDA_SANDBOX_RESOURCE_GROUP=$resource_group" \
    "EDA_SANDBOX_GROUP=$sandbox_group_name" \
    "EDA_SANDBOX_GROUP_ID=$sandbox_group_id" \
    "EDA_SANDBOX_REGION=$location" \
    "EDA_SANDBOX_DISK_IMAGE_ID=$sandbox_disk_image_id" \
    "EDA_SANDBOX_IMAGE_DIGEST=$sandbox_digest"; do
    name=${variable%%=*}
    value=${variable#*=}
    azd env set "$name" "$value" || fail "unable to persist $name"
done

api_app_name="${api_app_id##*/}"
az containerapp update \
    --name "$api_app_name" \
    --resource-group "$resource_group" \
    --set-env-vars "EDA_SANDBOX_IMAGE_DIGEST=$sandbox_digest" \
    --only-show-errors >/dev/null \
    || fail "unable to update the API with Sandbox readiness evidence"

printf '%s\n' "sandbox group deployed: ${sandbox_group_id}"
