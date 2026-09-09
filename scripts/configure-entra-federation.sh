#!/bin/sh
set -eu

fail() {
    printf '%s\n' "FAIL: $1" >&2
    exit 1
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || fail "required command is unavailable: $1"
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
tenant_id="$(azd_value ENTRA_TENANT_ID)"
client_id="$(azd_value ENTRA_CLIENT_ID)"
app_url="$(azd_value API_URL)"
web_principal_id="$(azd_value WEB_IDENTITY_PRINCIPAL_ID)"

case "$environment_name" in
    *[!a-z0-9-]* | '') fail 'AZURE_ENV_NAME must contain only lowercase letters, digits, and hyphens' ;;
esac
for identifier_name in tenant_id client_id web_principal_id; do
    eval "identifier_value=\${$identifier_name}"
    is_uuid "$identifier_value" || fail "$identifier_name must be a UUID"
done
case "$app_url" in
    https://* ) ;;
    *) fail 'API_URL must use HTTPS' ;;
esac
app_url="${app_url%/}"
redirect_uri="${app_url}/api/auth/callback"
fic_name="eda-web-${environment_name}"

app_json="$(az ad app show --id "$client_id" --only-show-errors)" || fail 'unable to read the Entra application in the active tenant'
app_object_id="$(printf '%s' "$app_json" | python3 -c '
import json
import sys

app = json.load(sys.stdin)
if app.get("signInAudience") != "AzureADMyOrg":
    raise SystemExit("application sign-in audience must be AzureADMyOrg")
if app.get("passwordCredentials") or app.get("keyCredentials"):
    raise SystemExit("application must not contain password or certificate credentials")
object_id = app.get("id")
if not isinstance(object_id, str):
    raise SystemExit("application object ID is missing")
print(object_id)
')" || fail 'application metadata does not satisfy the single-tenant credential-free policy'
is_uuid "$app_object_id" || fail 'application object ID is invalid'

if ! printf '%s' "$app_json" | python3 -c '
import json
import sys

redirects = json.load(sys.stdin).get("web", {}).get("redirectUris", [])
raise SystemExit(0 if redirects == [sys.argv[1]] else 1)
' "$redirect_uri"; then
    az ad app update --id "$app_object_id" --web-redirect-uris "$redirect_uri" --only-show-errors >/dev/null \
        || fail 'unable to set the web redirect URI'
fi

temporary_fic="$(mktemp)" || fail 'unable to create a temporary federation configuration'
trap 'rm -f "$temporary_fic"' EXIT HUP INT TERM
python3 - "$temporary_fic" "$fic_name" "$tenant_id" "$web_principal_id" <<'PY'
import json
import sys

path, name, tenant_id, principal_id = sys.argv[1:]
with open(path, "w", encoding="utf-8") as destination:
    json.dump(
        {
            "name": name,
            "issuer": f"https://login.microsoftonline.com/{tenant_id}/v2.0",
            "subject": principal_id,
            "description": "Trust the web UAMI as the BFF confidential client credential",
            "audiences": ["api://AzureADTokenExchange"],
        },
        destination,
        separators=(",", ":"),
    )
PY

existing_credentials="$(az ad app federated-credential list --id "$app_object_id" --only-show-errors)" \
    || fail 'unable to list application federated credentials'
if ! printf '%s' "$existing_credentials" | python3 -c '
import json
import sys

credentials = json.load(sys.stdin)
if not isinstance(credentials, list):
    raise SystemExit("federated credential list must be a list")
expected = {
    "name": sys.argv[1],
    "issuer": sys.argv[2],
    "subject": sys.argv[3],
    "audiences": ["api://AzureADTokenExchange"],
}
matching = [credential for credential in credentials if isinstance(credential, dict) and credential.get("name") == expected["name"]]
raise SystemExit(0 if len(matching) == 1 and all(matching[0].get(key) == value for key, value in expected.items()) else 1)
' "$fic_name" "https://login.microsoftonline.com/${tenant_id}/v2.0" "$web_principal_id"; then
    existing_names="$(printf '%s' "$existing_credentials" | python3 -c '
import json
import sys

for credential in json.load(sys.stdin):
    if isinstance(credential, dict) and isinstance(credential.get("name"), str):
        print(credential["name"])
')"
    for existing_name in $existing_names; do
        [ "$existing_name" = "$fic_name" ] || continue
        az ad app federated-credential delete --id "$app_object_id" --federated-credential-id "$existing_name" --only-show-errors >/dev/null \
            || fail 'unable to replace the existing web federated credential'
    done

    az ad app federated-credential create --id "$app_object_id" --parameters "$temporary_fic" --only-show-errors >/dev/null \
        || fail 'unable to create the web federated credential'
fi

verified_fic="$(az ad app federated-credential show --id "$app_object_id" --federated-credential-id "$fic_name" --only-show-errors)" \
    || fail 'unable to verify the web federated credential'
printf '%s' "$verified_fic" | python3 -c '
import json
import sys

credential = json.load(sys.stdin)
expected = {
    "issuer": sys.argv[1],
    "subject": sys.argv[2],
    "audiences": ["api://AzureADTokenExchange"],
}
for field, value in expected.items():
    if credential.get(field) != value:
        raise SystemExit(f"federated credential {field} does not match")
' "https://login.microsoftonline.com/${tenant_id}/v2.0" "$web_principal_id" \
    || fail 'web federated credential verification failed'

printf '%s\n' "Entra federation configured for ${environment_name}"