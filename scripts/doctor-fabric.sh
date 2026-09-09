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

phase=""
if [[ "${1:-}" == "--phase" ]]; then
    phase="${2:-}"
fi
[[ "$phase" == "predeploy" || "$phase" == "postdeploy" ]] \
    || fail "usage: doctor-fabric.sh --phase predeploy|postdeploy"

if [[ "${FABRIC_ENABLED:-false}" == "false" ]]; then
    printf '%s\n' "SKIP: Fabric intentionally disabled"
    exit 0
fi
[[ "${FABRIC_ENABLED:-}" == "true" ]] || fail "FABRIC_ENABLED must be true or false"

for command_name in az azd jq python3 sha256sum stat curl; do
    require_command "$command_name"
done
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
done
for name in \
    FABRIC_AZURE_CONFIG_DIR \
    PRODUCT_AZURE_CONFIG_DIR \
    FABRIC_TENANT_ID \
    AZURE_TENANT_ID \
    FABRIC_CLIENT_ID \
    FABRIC_SEMANTIC_MODELS_JSON; do
    require_value "$name"
done
require_private_context "$FABRIC_AZURE_CONFIG_DIR"
require_private_context "$PRODUCT_AZURE_CONFIG_DIR"

fabric_active_tenant="$(fabric_az account show --query tenantId --output tsv --only-show-errors)"
product_active_tenant="$(product_az account show --query tenantId --output tsv --only-show-errors)"
[[ "$fabric_active_tenant" == "$FABRIC_TENANT_ID" ]] || fail "Fabric CLI context tenant mismatch"
[[ "$product_active_tenant" == "$AZURE_TENANT_ID" ]] || fail "product CLI context tenant mismatch"
[[ "$FABRIC_TENANT_ID" != "$AZURE_TENANT_ID" ]] || fail "cross-tenant acceptance requires distinct tenant IDs"

app_json="$(fabric_az ad app show --id "$FABRIC_CLIENT_ID" --output json --only-show-errors)" \
    || fail "Fabric OAuth app is unavailable"
[[ "$(jq -r '.signInAudience // empty' <<<"$app_json")" == "AzureADMyOrg" ]] \
    || fail "Fabric OAuth app must be single tenant"
[[ "$(jq '.passwordCredentials | length' <<<"$app_json")" == "0" ]] \
    || fail "Fabric OAuth app must not have password credentials"

power_bi_app_id="00000009-0000-0000-c000-000000000000"
power_bi_sp="$(fabric_az ad sp show --id "$power_bi_app_id" --output json --only-show-errors)" \
    || fail "Power BI service principal is unavailable"
APP_JSON="$app_json" POWER_BI_SP="$power_bi_sp" python3 <<'PY'
import json
import os

app = json.loads(os.environ["APP_JSON"])
service_principal = json.loads(os.environ["POWER_BI_SP"])
scope_ids = {
    scope.get("value"): scope.get("id")
    for scope in service_principal.get("oauth2PermissionScopes", [])
    if isinstance(scope, dict) and scope.get("isEnabled") is True
}
required_ids = {scope_ids.get("Item.Read.All"), scope_ids.get("Item.Execute.All")}
if None in required_ids:
    raise SystemExit("required Power BI delegated scopes are unavailable")
configured = {
    access.get("id")
    for resource in app.get("requiredResourceAccess", [])
    if resource.get("resourceAppId") == "00000009-0000-0000-c000-000000000000"
    for access in resource.get("resourceAccess", [])
    if access.get("type") == "Scope"
}
if configured != required_ids:
    raise SystemExit("Fabric OAuth app delegated scopes do not exactly match")
PY

FABRIC_CATALOG="$FABRIC_SEMANTIC_MODELS_JSON" python3 <<'PY'
import json
import os
import re
import uuid

catalog = json.loads(os.environ["FABRIC_CATALOG"])
if not isinstance(catalog, dict) or not 1 <= len(catalog) <= 20:
    raise SystemExit("Fabric semantic-model catalog must contain 1-20 aliases")
seen = set()
for alias, target in catalog.items():
    if not isinstance(alias, str) or re.fullmatch(r"[a-z][a-z0-9-]{1,39}", alias) is None:
        raise SystemExit("Fabric catalog alias is invalid")
    if not isinstance(target, dict) or set(target) - {"modelId", "description", "routingTerms"}:
        raise SystemExit("Fabric catalog target is malformed")
    identifier = str(uuid.UUID(target.get("modelId", "")))
    if identifier in seen:
        raise SystemExit("Fabric semantic-model UUID is duplicated")
    seen.add(identifier)
    description = target.get("description")
    if not isinstance(description, str) or not 1 <= len(description) <= 240 or any(ord(c) < 32 for c in description):
        raise SystemExit("Fabric catalog description is invalid")
PY

tenant_hash="$(printf '%s' "$FABRIC_TENANT_ID" | sha256sum | cut -d' ' -f1)"
catalog_hash="$(printf '%s' "$FABRIC_SEMANTIC_MODELS_JSON" | sha256sum | cut -d' ' -f1)"
if [[ "$phase" == "predeploy" ]]; then
    printf '%s\n' "PASS: Fabric predeploy tenant_sha256=${tenant_hash} catalog_sha256=${catalog_hash}"
    exit 0
fi

for name in FABRIC_KEY_VAULT_URL FABRIC_SIGNING_CERTIFICATE_NAME API_URL FABRIC_ACCEPTANCE_USERS_JSON; do
    require_value "$name"
done
vault_host="${FABRIC_KEY_VAULT_URL#https://}"
vault_name="${vault_host%%.*}"
[[ -n "$vault_name" && "$vault_host" == *.vault.azure.net* ]] || fail "FABRIC_KEY_VAULT_URL is invalid"
callback_uri="${API_URL%/}/api/fabric/auth/callback"
certificate_json="$(product_az keyvault certificate show \
    --vault-name "$vault_name" \
    --name "$FABRIC_SIGNING_CERTIFICATE_NAME" \
    --output json \
    --only-show-errors)" || fail "Fabric OAuth certificate is unavailable"
thumbprint_hex="$(jq -r '.x509ThumbprintHex // empty' <<<"$certificate_json")"
[[ -n "$thumbprint_hex" ]] || fail "Fabric OAuth certificate thumbprint is unavailable"

redirect_match="$(jq --arg callback "$callback_uri" '[.web.redirectUris[]? | select(. == $callback)] | length' <<<"$app_json")"
[[ "$redirect_match" == "1" ]] || fail "Fabric OAuth callback does not match the deployed API"
[[ "$(jq '.keyCredentials | length' <<<"$app_json")" -ge 1 ]] || fail "Fabric OAuth public certificate credential is absent"

FABRIC_USERS="$FABRIC_ACCEPTANCE_USERS_JSON" python3 <<'PY'
import json
import os
import uuid

users = json.loads(os.environ["FABRIC_USERS"])
if not isinstance(users, dict) or set(users) != {"rlsA", "rlsB", "noBuild"}:
    raise SystemExit("acceptance users must contain rlsA, rlsB, and noBuild")
for value in users.values():
    uuid.UUID(value)
PY

consent_count="$(fabric_az rest \
    --method GET \
    --url "https://graph.microsoft.com/v1.0/oauth2PermissionGrants?\$filter=clientId eq '$(fabric_az ad sp show --id "$FABRIC_CLIENT_ID" --query id -o tsv --only-show-errors)'" \
    --query 'length(value)' \
    --output tsv \
    --only-show-errors)" || fail "unable to inspect delegated admin consent"
[[ "$consent_count" -ge 1 ]] || fail "Fabric OAuth delegated permissions have no admin consent grant"

endpoint_code="$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' \
    --max-time 20 \
    'https://api.fabric.microsoft.com/v1/mcp/fabricaihub/integrations/m365')" \
    || fail "fixed Fabric IQ endpoint is unreachable"
[[ "$endpoint_code" == "401" || "$endpoint_code" == "403" || "$endpoint_code" == "405" ]] \
    || fail "fixed Fabric IQ endpoint returned an unexpected status"

callback_hash="$(printf '%s' "$callback_uri" | sha256sum | cut -d' ' -f1)"
thumbprint_hash="$(printf '%s' "$thumbprint_hex" | sha256sum | cut -d' ' -f1)"
printf '%s\n' "PASS: Fabric postdeploy tenant_sha256=${tenant_hash} catalog_sha256=${catalog_hash} callback_sha256=${callback_hash} certificate_sha256=${thumbprint_hash} users=3"
