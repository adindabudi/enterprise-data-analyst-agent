#!/bin/sh
set -eu

ROLE_ID='cc6305dc-7f9b-4f48-9d87-08e1c83f8e72'

fail() {
    printf '%s\n' "FAIL: $1" >&2
    exit 1
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || fail "required command is unavailable: $1"
}

manual_graph_steps() {
    printf '%s\n' 'Manual action required: grant Microsoft Graph Application.ReadWrite.All to the deployer, then rerun this command.' >&2
    printf '%s\n' 'After provisioning, assign the Branding administrator app role to approved tenant users in Enterprise applications.' >&2
}

is_uuid() {
    case "$1" in
        ????????-????-????-????-????????????) ;;
        *) return 1 ;;
    esac
}

for command_name in az azd python3; do
    require_command "$command_name"
done

azd_values="$(azd env get-values)" || fail 'unable to read azd environment values'
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
case "$environment_name" in
    *[!a-z0-9-]* | '') fail 'AZURE_ENV_NAME must contain only lowercase letters, digits, and hyphens' ;;
esac

active_tenant_id="$(az account show --query tenantId --output tsv --only-show-errors)" || fail 'unable to read the active Entra tenant'
is_uuid "$active_tenant_id" || fail 'the active Azure account did not return a valid tenant ID'

app_client_id="${EDA_ENTRA_APP_CLIENT_ID-}"
if [ -n "$app_client_id" ]; then
    is_uuid "$app_client_id" || fail 'EDA_ENTRA_APP_CLIENT_ID must be an application client ID'
    app_json="$(az ad app show --id "$app_client_id" --only-show-errors)" || fail 'the explicit EDA_ENTRA_APP_CLIENT_ID is not registered in the active tenant'
else
    app_json="$(az ad app create --display-name "eda-${environment_name}" --sign-in-audience AzureADMyOrg --only-show-errors)" || {
        manual_graph_steps
        fail 'unable to create the single-tenant application registration'
    }
fi

app_fields="$(printf '%s' "$app_json" | python3 -c '
import json
import sys

app = json.load(sys.stdin)
if app.get("signInAudience") != "AzureADMyOrg":
    raise SystemExit("application sign-in audience must be AzureADMyOrg")
if app.get("passwordCredentials") or app.get("keyCredentials"):
    raise SystemExit("application must not contain password or certificate credentials")
object_id = app.get("id")
client_id = app.get("appId")
if not isinstance(object_id, str) or not isinstance(client_id, str):
    raise SystemExit("application metadata is incomplete")
print(f"{object_id} {client_id}")
')" || fail 'application metadata does not satisfy the single-tenant credential-free policy'
set -- $app_fields
app_object_id="$1"
app_client_id="$2"
is_uuid "$app_object_id" || fail 'application object ID is invalid'
is_uuid "$app_client_id" || fail 'application client ID is invalid'

app_roles="$(printf '%s' "$app_json" | python3 -c '
import json
import sys

role_id = "cc6305dc-7f9b-4f48-9d87-08e1c83f8e72"
required = {
    "allowedMemberTypes": ["User"],
    "description": "Publish validated tenant branding",
    "displayName": "Branding administrator",
    "id": role_id,
    "isEnabled": True,
    "value": "Branding.Admin",
}
app = json.load(sys.stdin)
roles = app.get("appRoles", [])
if not isinstance(roles, list):
    raise SystemExit("application appRoles must be a list")
if not all(isinstance(role, dict) for role in roles):
    raise SystemExit("application appRoles must contain objects")
writable_fields = tuple(required)
roles = [{field: role.get(field) for field in writable_fields} for role in roles]
matching = [role for role in roles if role.get("value") == "Branding.Admin"]
if matching and matching != [required]:
    raise SystemExit("Branding.Admin must retain its stable definition and UUID")
if not matching:
    roles.append(required)
print(json.dumps(roles, separators=(",", ":")))
')" || fail 'unable to build the Branding.Admin role configuration'

if ! printf '%s' "$app_json" | python3 -c '
import json
import sys

required = {
    "allowedMemberTypes": ["User"],
    "description": "Publish validated tenant branding",
    "displayName": "Branding administrator",
    "id": "cc6305dc-7f9b-4f48-9d87-08e1c83f8e72",
    "isEnabled": True,
    "value": "Branding.Admin",
}
roles = json.load(sys.stdin).get("appRoles", [])
writable_fields = tuple(required)
roles = [{field: role.get(field) for field in writable_fields} for role in roles]
raise SystemExit(0 if roles == [required] else 1)
'; then
    if ! az ad app update --id "$app_object_id" --app-roles "$app_roles" --only-show-errors >/dev/null; then
        manual_graph_steps
        fail 'unable to create or retain the Branding.Admin app role'
    fi
fi

azd env set ENTRA_CLIENT_ID "$app_client_id" || fail 'unable to persist ENTRA_CLIENT_ID'
azd env set ENTRA_TENANT_ID "$active_tenant_id" || fail 'unable to persist ENTRA_TENANT_ID'
azd env set AZURE_ENTRA_CLIENT_ID "$app_client_id" || fail 'unable to persist AZURE_ENTRA_CLIENT_ID for Bicep'

printf '%s\n' "Entra application configured for ${environment_name}"