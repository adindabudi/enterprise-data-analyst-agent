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

require_private_context() {
    local path="$1"
    [[ -d "$path" ]] || fail "isolated Azure CLI context does not exist"
    [[ "$(stat -c '%a' "$path")" == "700" ]] || fail "isolated Azure CLI context must have mode 0700"
}

fabric_az() {
    AZURE_CONFIG_DIR="$FABRIC_AZURE_CONFIG_DIR" az "$@"
}

product_az() {
    AZURE_CONFIG_DIR="$PRODUCT_AZURE_CONFIG_DIR" az "$@"
}

require_command az
require_command azd
require_command jq
require_command python3
require_command sha256sum
require_command stat

phase="${1:-}"
[[ "$phase" == "bootstrap" || "$phase" == "finalize" ]] || fail "usage: configure-fabric-entra.sh bootstrap|finalize"
if [[ "${FABRIC_ENABLED:-false}" == "false" ]]; then
    printf '%s\n' "SKIP: Fabric intentionally disabled"
    exit 0
fi
[[ "${FABRIC_ENABLED:-}" == "true" ]] || fail "FABRIC_ENABLED must be true or false"
[[ "${FABRIC_PROVIDER:-}" == "semantic_model" || "${FABRIC_PROVIDER:-}" == "ontology" ]] \
    || fail "FABRIC_PROVIDER must be semantic_model or ontology"

for name in FABRIC_AZURE_CONFIG_DIR PRODUCT_AZURE_CONFIG_DIR FABRIC_TENANT_ID AZURE_TENANT_ID AZURE_ENV_NAME; do
    require_value "$name"
done
require_private_context "$FABRIC_AZURE_CONFIG_DIR"
require_private_context "$PRODUCT_AZURE_CONFIG_DIR"

fabric_active_tenant="$(fabric_az account show --query tenantId --output tsv --only-show-errors)"
product_active_tenant="$(product_az account show --query tenantId --output tsv --only-show-errors)"
[[ "$fabric_active_tenant" == "$FABRIC_TENANT_ID" ]] || fail "Fabric CLI context tenant mismatch"
[[ "$product_active_tenant" == "$AZURE_TENANT_ID" ]] || fail "product CLI context tenant mismatch"
fabric_topology="${FABRIC_TOPOLOGY:-cross_tenant}"
[[ "$fabric_topology" == "cross_tenant" || "$fabric_topology" == "same_tenant_smoke" ]] \
    || fail "FABRIC_TOPOLOGY must be cross_tenant or same_tenant_smoke"
if [[ "$fabric_topology" == "cross_tenant" ]]; then
    [[ "$FABRIC_TENANT_ID" != "$AZURE_TENANT_ID" ]] \
        || fail "cross-tenant Fabric setup requires distinct tenant IDs"
else
    [[ "$FABRIC_TENANT_ID" == "$AZURE_TENANT_ID" ]] \
        || fail "same-tenant Fabric smoke requires equal tenant IDs"
fi

app_display_name="eda-fabric-${AZURE_ENV_NAME}"
power_bi_app_id="00000009-0000-0000-c000-000000000000"
read_scope="Item.Read.All"
client_id="${FABRIC_CLIENT_ID:-}"

find_app() {
    if [[ -n "$client_id" ]]; then
        fabric_az ad app show --id "$client_id" --output json --only-show-errors
        return
    fi
    fabric_az ad app list --display-name "$app_display_name" --output json --only-show-errors \
        | python3 -c '
import json
import sys

apps = json.load(sys.stdin)
if len(apps) > 1:
    raise SystemExit("multiple Fabric OAuth apps use the configured display name")
print(json.dumps(apps[0]) if apps else "")
'
}

if [[ "$phase" == "bootstrap" ]]; then
    app_json="$(find_app)" || fail "unable to resolve the Fabric OAuth app"
    if [[ -z "$app_json" ]]; then
        client_id="$(fabric_az ad app create \
            --display-name "$app_display_name" \
            --sign-in-audience AzureADMyOrg \
            --query appId \
            --output tsv \
            --only-show-errors)" || fail "unable to create the single-tenant Fabric OAuth app"
        app_json="$(fabric_az ad app show --id "$client_id" --output json --only-show-errors)"
    else
        client_id="$(jq -r '.appId // empty' <<<"$app_json")"
    fi
    [[ -n "$client_id" ]] || fail "Fabric OAuth client ID is unavailable"
    app_object_id="$(jq -r '.id // empty' <<<"$app_json")"
    [[ "$(jq -r '.signInAudience // empty' <<<"$app_json")" == "AzureADMyOrg" ]] \
        || fail "Fabric OAuth app must be single tenant"
    [[ "$(jq '.passwordCredentials | length' <<<"$app_json")" == "0" ]] \
        || fail "Fabric OAuth app must not have password credentials"
    if ! fabric_az ad sp show --id "$client_id" --output none --only-show-errors >/dev/null 2>&1; then
        fabric_az ad sp create --id "$client_id" --output none --only-show-errors \
            || fail "unable to create the Fabric OAuth service principal"
    fi

    scopes_json="$(fabric_az ad sp show --id "$power_bi_app_id" --output json --only-show-errors)" \
        || fail "Power BI service principal is unavailable in the Fabric tenant"
    temporary_access="$(mktemp)" || fail "unable to create delegated-scope request"
    trap 'rm -f "$temporary_access"' EXIT HUP INT TERM
    chmod 600 "$temporary_access"
    SCOPES_JSON="$scopes_json" python3 - "$temporary_access" "$power_bi_app_id" "$read_scope" <<'PY'
import json
import os
import sys

path, resource_app_id, read_scope = sys.argv[1:]
service_principal = json.loads(os.environ["SCOPES_JSON"])
by_value = {
    scope.get("value"): scope.get("id")
    for scope in service_principal.get("oauth2PermissionScopes", [])
    if isinstance(scope, dict) and scope.get("isEnabled") is True
}
required = (read_scope, "Item.Execute.All")
if any(not isinstance(by_value.get(value), str) for value in required):
    raise SystemExit("required Power BI delegated scopes are unavailable")
with open(path, "w", encoding="utf-8") as destination:
    json.dump(
        [{
            "resourceAppId": resource_app_id,
            "resourceAccess": [{"id": by_value[value], "type": "Scope"} for value in required],
        }],
        destination,
        separators=(",", ":"),
    )
PY
    if ! APP_JSON="$app_json" SCOPES_JSON="$scopes_json" python3 - "$temporary_access" "$fabric_topology" <<'PY'
import json
import os
import sys

expected_path, topology = sys.argv[1:]
app = json.loads(os.environ["APP_JSON"])
service_principal = json.loads(os.environ["SCOPES_JSON"])
expected = json.loads(open(expected_path, encoding="utf-8").read())

def normalized(value):
    return sorted(
        (
            item.get("resourceAppId"),
            tuple(sorted((entry.get("id"), entry.get("type")) for entry in item.get("resourceAccess", []))),
        )
        for item in value
    )

actual = app.get("requiredResourceAccess", [])
if normalized(actual) == normalized(expected):
    raise SystemExit(0)
if topology != "same_tenant_smoke":
    raise SystemExit(1)
scope_ids = {
    scope.get("value"): scope.get("id")
    for scope in service_principal.get("oauth2PermissionScopes", [])
    if isinstance(scope, dict) and scope.get("isEnabled") is True
}
legacy = [{
    "resourceAppId": "00000009-0000-0000-c000-000000000000",
    "resourceAccess": [
        {"id": scope_ids.get("Item.ReadWrite.All"), "type": "Scope"},
        {"id": scope_ids.get("Item.Execute.All"), "type": "Scope"},
    ],
}]
raise SystemExit(0 if normalized(actual) == normalized(legacy) else 1)
PY
    then
        fabric_az ad app update \
            --id "$app_object_id" \
            --required-resource-accesses "@$temporary_access" \
            --only-show-errors >/dev/null || fail "unable to set Power BI delegated scopes"
    elif [[ "$fabric_topology" == "same_tenant_smoke" ]]; then
        printf '%s\n' "SKIP: same-tenant smoke retains its existing non-production Fabric grant"
    fi
    azd env set FABRIC_CLIENT_ID "$client_id" >/dev/null || fail "unable to persist the Fabric client ID"
    printf '%s\n' "Fabric OAuth bootstrap complete"
    exit 0
fi

for name in FABRIC_CLIENT_ID FABRIC_KEY_VAULT_URL API_URL; do
    if [[ -z "${!name:-}" ]]; then
        value="$(azd env get-value "$name" 2>/dev/null || true)"
        if [[ -z "$value" && "$name" == "FABRIC_KEY_VAULT_URL" ]]; then
            value="$(azd env get-value FABRIC_VAULT_URL 2>/dev/null || true)"
        fi
        if [[ -n "$value" ]]; then
            printf -v "$name" '%s' "$value"
            export "$name"
        fi
    fi
    require_value "$name"
done
client_id="$FABRIC_CLIENT_ID"
app_json="$(fabric_az ad app show --id "$client_id" --output json --only-show-errors)" \
    || fail "Fabric OAuth app is unavailable"
app_object_id="$(jq -r '.id // empty' <<<"$app_json")"
[[ -n "$app_object_id" ]] || fail "Fabric OAuth app object ID is unavailable"
[[ "$(jq '.passwordCredentials | length' <<<"$app_json")" == "0" ]] \
    || fail "Fabric OAuth app must not have password credentials"

certificate_name="${FABRIC_SIGNING_CERTIFICATE_NAME:-fabric-oauth-signing}"
callback_uri="${API_URL%/}/api/fabric/auth/callback"
if [[ -z "${FABRIC_PUBLIC_CERTIFICATE_FILE:-}" ]] && APP_JSON="$app_json" python3 - "$certificate_name" "$callback_uri" <<'PY'
import json
import os
import sys
from datetime import UTC, datetime

certificate_name, callback_uri = sys.argv[1:]
app = json.loads(os.environ["APP_JSON"])
credentials = app.get("keyCredentials", [])
redirects = app.get("web", {}).get("redirectUris", [])
if redirects != [callback_uri] or len(credentials) != 1:
    raise SystemExit(1)
credential = credentials[0]
expires = credential.get("endDateTime")
if (
    credential.get("displayName") != certificate_name
    or credential.get("usage") != "Verify"
    or not isinstance(expires, str)
):
    raise SystemExit(1)
try:
    expiry = datetime.fromisoformat(expires.replace("Z", "+00:00"))
except ValueError:
    raise SystemExit(1) from None
if expiry <= datetime.now(UTC):
    raise SystemExit(1)
PY
then
    printf '%s\n' "Fabric OAuth finalize already complete"
    printf '%s\n' "Consent required: continue with user consent when tenant policy allows; otherwise request administrator consent for ${read_scope} and Item.Execute.All"
    exit 0
fi

vault_host="${FABRIC_KEY_VAULT_URL#https://}"
vault_name="${vault_host%%.*}"
[[ -n "$vault_name" && "$vault_host" == *.vault.azure.net* ]] || fail "FABRIC_KEY_VAULT_URL is invalid"
temporary_certificate=""
cleanup_certificate() {
    if [[ -n "$temporary_certificate" ]]; then
        rm -f "$temporary_certificate"
    fi
}
trap cleanup_certificate EXIT HUP INT TERM

if [[ -n "${FABRIC_PUBLIC_CERTIFICATE_FILE:-}" ]]; then
    certificate_file="$FABRIC_PUBLIC_CERTIFICATE_FILE"
    [[ -r "$certificate_file" ]] || fail "FABRIC_PUBLIC_CERTIFICATE_FILE must be readable"
    thumbprint_hex="$(python3 - "$certificate_file" <<'PY'
import hashlib
import ssl
import sys
from pathlib import Path

certificate_pem = Path(sys.argv[1]).read_text(encoding="ascii")
if "PRIVATE KEY" in certificate_pem:
    raise SystemExit("public certificate file must not contain a private key")
certificate_der = ssl.PEM_cert_to_DER_cert(certificate_pem)
print(hashlib.sha1(certificate_der).hexdigest().upper())
PY
)" || fail "unable to validate the public Fabric OAuth certificate"
else
    temporary_certificate="$(mktemp)" || fail "unable to create a temporary public certificate file"
    chmod 600 "$temporary_certificate"
    certificate_file="$temporary_certificate"
    product_az keyvault certificate download \
        --vault-name "$vault_name" \
        --name "$certificate_name" \
        --file "$certificate_file" \
        --encoding PEM \
        --only-show-errors >/dev/null || fail "unable to download the public Fabric OAuth certificate"
    thumbprint_hex="$(product_az keyvault certificate show \
        --vault-name "$vault_name" \
        --name "$certificate_name" \
        --query x509ThumbprintHex \
        --output tsv \
        --only-show-errors)" || fail "unable to read the public certificate thumbprint"
fi
thumbprint_base64="$(python3 - "$thumbprint_hex" <<'PY'
import base64
import sys
print(base64.b64encode(bytes.fromhex(sys.argv[1])).decode("ascii"))
PY
)"

fabric_az ad app update --id "$app_object_id" --web-redirect-uris "$callback_uri" --only-show-errors >/dev/null \
    || fail "unable to set the Fabric callback URI"
if ! jq -e --arg thumbprint "$thumbprint_base64" '.keyCredentials[]? | select(.customKeyIdentifier == $thumbprint)' \
    <<<"$app_json" >/dev/null; then
    fabric_az ad app credential reset \
        --id "$app_object_id" \
        --cert "@$certificate_file" \
        --append \
        --display-name "$certificate_name" \
        --only-show-errors >/dev/null || fail "unable to upload the public certificate credential"
fi

callback_hash="$(printf '%s' "$callback_uri" | sha256sum | cut -d' ' -f1)"
thumbprint_hash="$(printf '%s' "$thumbprint_hex" | sha256sum | cut -d' ' -f1)"
printf '%s\n' "Fabric OAuth finalize complete callback_sha256=${callback_hash} certificate_sha256=${thumbprint_hash}"
printf '%s\n' "Consent required: continue with user consent when tenant policy allows; otherwise request administrator consent for ${read_scope} and Item.Execute.All"
