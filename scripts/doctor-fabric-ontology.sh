#!/bin/sh
set -eu

fail() {
    printf '%s\n' "FAIL: $1" >&2
    exit 1
}

pass() {
    printf '%s\n' "PASS: $1"
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || fail "required command is unavailable: $1"
}

require_value() {
    variable_name="$1"
    eval "variable_value=\${$variable_name:-}"
    [ -n "$variable_value" ] || fail "$variable_name must be set"
}

require_uuid() {
    value="$1"
    label="$2"
    printf '%s' "$value" | grep -Eiq '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' \
        || fail "$label must be a UUID"
}

temporary_azure_config="$(mktemp -d)"
chmod 700 "$temporary_azure_config"
cleanup() {
    rm -rf "$temporary_azure_config"
}
trap cleanup EXIT HUP INT TERM

# An externally prepared isolated context is optional; this script never logs in,
# logs out, switches accounts, or writes to the caller's Azure CLI context.
if [ -n "${FABRIC_ONTOLOGY_AZURE_CONFIG_DIR:-}" ]; then
    [ -d "$FABRIC_ONTOLOGY_AZURE_CONFIG_DIR" ] || fail 'FABRIC_ONTOLOGY_AZURE_CONFIG_DIR must be a directory'
    [ "$(stat -c '%a' "$FABRIC_ONTOLOGY_AZURE_CONFIG_DIR")" = '700' ] \
        || fail 'FABRIC_ONTOLOGY_AZURE_CONFIG_DIR must have mode 0700'
    AZURE_CONFIG_DIR="$FABRIC_ONTOLOGY_AZURE_CONFIG_DIR"
else
    AZURE_CONFIG_DIR="$temporary_azure_config"
fi
export AZURE_CONFIG_DIR

require_command az
require_command curl
require_command jq
require_value FOUNDRY_TENANT_ID
require_value FABRIC_ONTOLOGY_TENANT_ID
require_value FABRIC_PROVIDER
require_value FABRIC_ONTOLOGY_CATALOG_PATH
require_value FABRIC_ONTOLOGY_ALLOWED_USER_OBJECT_ID
require_value FABRIC_ONTOLOGY_DENIED_USER_OBJECT_ID
require_value FABRIC_ONTOLOGY_CAPACITY_EVIDENCE_PATH

[ "$FABRIC_PROVIDER" = 'ontology' ] || fail 'FABRIC_PROVIDER must be ontology'
require_uuid "$FOUNDRY_TENANT_ID" FOUNDRY_TENANT_ID
require_uuid "$FABRIC_ONTOLOGY_TENANT_ID" FABRIC_ONTOLOGY_TENANT_ID
[ "$FOUNDRY_TENANT_ID" != "$FABRIC_ONTOLOGY_TENANT_ID" ] || fail 'Foundry and Fabric tenants must differ'
require_uuid "$FABRIC_ONTOLOGY_ALLOWED_USER_OBJECT_ID" FABRIC_ONTOLOGY_ALLOWED_USER_OBJECT_ID
require_uuid "$FABRIC_ONTOLOGY_DENIED_USER_OBJECT_ID" FABRIC_ONTOLOGY_DENIED_USER_OBJECT_ID
[ "$FABRIC_ONTOLOGY_ALLOWED_USER_OBJECT_ID" != "$FABRIC_ONTOLOGY_DENIED_USER_OBJECT_ID" ] \
    || fail 'allowed and denied B2B fixture users must differ'

[ -r "$FABRIC_ONTOLOGY_CATALOG_PATH" ] || fail 'FABRIC_ONTOLOGY_CATALOG_PATH must be readable'
[ -r "$FABRIC_ONTOLOGY_CAPACITY_EVIDENCE_PATH" ] || fail 'FABRIC_ONTOLOGY_CAPACITY_EVIDENCE_PATH must be readable'
[ -r 'tests/fixtures/fabric/lamna-healthcare/fixture-lock.json' ] || fail 'Lamna fixture lock is missing'

catalog_count="$(jq 'if type == "object" then length else -1 end' "$FABRIC_ONTOLOGY_CATALOG_PATH")"
[ "$catalog_count" -eq 1 ] || fail 'ontology catalog must contain exactly one selected target'
workspace_id="$(jq -r 'to_entries[0].value.workspaceId // empty' "$FABRIC_ONTOLOGY_CATALOG_PATH")"
ontology_id="$(jq -r 'to_entries[0].value.ontologyId // empty' "$FABRIC_ONTOLOGY_CATALOG_PATH")"
require_uuid "$workspace_id" 'catalog workspaceId'
require_uuid "$ontology_id" 'catalog ontologyId'
[ "$workspace_id" != "$ontology_id" ] || fail 'catalog workspaceId and ontologyId must differ'

capacity_tier="$(jq -r '.sku // .capacitySku // empty' "$FABRIC_ONTOLOGY_CAPACITY_EVIDENCE_PATH")"
case "$capacity_tier" in
    F[2-9] | F[1-9][0-9] | F[1-9][0-9][0-9] | P[1-9] | P[1-9][0-9]) ;;
    *) fail 'paid F2+ or P1+ capacity evidence is required for MCP acceptance' ;;
esac

fixture_hash="$(jq -r '.sha256 // empty' tests/fixtures/fabric/lamna-healthcare/fixture-lock.json)"
[ "$fixture_hash" = '10e9f03a7361cdbdea5feac216b6762a5b7b22378a15893a0167c96b54dd9bf2' ] \
    || fail 'Lamna fixture lock hash does not match the pinned fixture'

access_token="$(az account get-access-token --tenant "$FABRIC_ONTOLOGY_TENANT_ID" \
    --resource 'https://api.fabric.microsoft.com' --query accessToken --output tsv --only-show-errors)" \
    || fail 'unable to acquire a Fabric token from the isolated Azure CLI context'
[ -n "$access_token" ] || fail 'Fabric token was empty'

endpoint="https://api.fabric.microsoft.com/v1/mcp/dataPlane/workspaces/$workspace_id/items/$ontology_id/ontologyEndpoint"
contract="$(curl --fail --silent --show-error --request POST "$endpoint" \
    --header "Authorization: Bearer $access_token" \
    --header 'Content-Type: application/json' \
    --data '{"method":"tools/list","params":{}}')" \
    || fail 'published ontology MCP endpoint was not reachable'
tool_count="$(printf '%s' "$contract" | jq '[.result.tools[]?.name] | length')"
exact_tools="$(printf '%s' "$contract" | jq -r '[.result.tools[]?.name] | sort | join(",")')"
[ "$tool_count" -eq 2 ] || fail 'ontology MCP contract must expose exactly two tools'
[ "$exact_tools" = 'list_ontology_entity_types,search_ontology' ] || fail 'ontology MCP tool contract drifted'

entity_count="$(curl --fail --silent --show-error --request POST "$endpoint" \
    --header "Authorization: Bearer $access_token" \
    --header 'Content-Type: application/json' \
    --data '{"method":"tools/call","params":{"name":"list_ontology_entity_types","arguments":{"includeProperties":true}}}')" \
    || fail 'unable to inspect ontology entity keys'
printf '%s' "$entity_count" | jq -e '.result != null' >/dev/null || fail 'entity inspection response was malformed'

# A no-data authorization probe is passed as an externally generated report so
# this doctor never obtains or handles the denied user's credentials.
[ -r "${FABRIC_ONTOLOGY_DENIED_PROBE_PATH:-}" ] || fail 'FABRIC_ONTOLOGY_DENIED_PROBE_PATH must be readable'
jq -e '.authorized == false and (.resultArtifactCreated == false)' "$FABRIC_ONTOLOGY_DENIED_PROBE_PATH" >/dev/null \
    || fail 'denied-user probe must prove no data and no result artifact'

pass "tenant hashes verified: $(printf '%s' "$FOUNDRY_TENANT_ID" | sha256sum | cut -c1-12)/$(printf '%s' "$FABRIC_ONTOLOGY_TENANT_ID" | sha256sum | cut -c1-12)"
pass "catalog targets: $catalog_count"
pass "fixture hash: $(printf '%s' "$fixture_hash" | cut -c1-12)"
pass "MCP tools: $tool_count"
pass 'denied B2B probe: no data and no artifact'