#!/bin/sh
set -eu

API_ROOT='https://api.fabric.microsoft.com/v1'
EXPECTED_PREFIX='eda-ontology-acceptance-'
WORKSPACE_ID=''
CONFIRM_WORKSPACE_ID=''
EXPECTED_NAME=''
OWNERSHIP_MANIFEST=''
EXECUTE=false

fail() {
    printf '%s\n' "FAIL: $1" >&2
    exit 1
}

usage() {
    cat <<'EOF'
Usage: cleanup-fabric-ontology-lab.sh --workspace-id UUID --expected-name NAME [options]

Defaults to a non-contacting dry run. Destructive execution additionally requires:
  --confirm-workspace-id UUID  equal to --workspace-id
  --ownership-manifest PATH    local fixture ownership proof
  --execute                    explicitly enable deletion

The expected name must be eda-ontology-acceptance-<suffix>. This command never
deletes resource groups, capacities, Entra applications, or B2B users.
EOF
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || fail "required command is unavailable: $1"
}

require_uuid() {
    value="$1"
    label="$2"
    printf '%s' "$value" | grep -Eiq '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' \
        || fail "$label must be a UUID"
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --workspace-id) WORKSPACE_ID="${2:-}"; shift 2 ;;
        --confirm-workspace-id) CONFIRM_WORKSPACE_ID="${2:-}"; shift 2 ;;
        --expected-name) EXPECTED_NAME="${2:-}"; shift 2 ;;
        --ownership-manifest) OWNERSHIP_MANIFEST="${2:-}"; shift 2 ;;
        --execute) EXECUTE=true; shift ;;
        --help) usage; exit 0 ;;
        *) fail "unsupported argument: $1" ;;
    esac
done

[ -n "$WORKSPACE_ID" ] || fail '--workspace-id is required'
[ -n "$EXPECTED_NAME" ] || fail '--expected-name is required'
case "$EXPECTED_NAME" in
    "$EXPECTED_PREFIX"?*) ;;
    *) fail "--expected-name must start with $EXPECTED_PREFIX" ;;
esac
require_uuid "$WORKSPACE_ID" --workspace-id

if [ "$EXECUTE" = false ]; then
    printf '%s\n' "DRY RUN: would delete only workspace $WORKSPACE_ID named $EXPECTED_NAME after explicit confirmation"
    exit 0
fi

[ "$CONFIRM_WORKSPACE_ID" = "$WORKSPACE_ID" ] || fail '--confirm-workspace-id must exactly match --workspace-id'
[ -n "$OWNERSHIP_MANIFEST" ] && [ -r "$OWNERSHIP_MANIFEST" ] || fail '--ownership-manifest must be a readable file'
require_command az
require_command curl
require_command jq

[ -n "${FABRIC_ONTOLOGY_TENANT_ID:-}" ] || fail 'FABRIC_ONTOLOGY_TENANT_ID must be set'
require_uuid "$FABRIC_ONTOLOGY_TENANT_ID" FABRIC_ONTOLOGY_TENANT_ID

manifest_workspace_id="$(jq -r '.workspaceId // empty' "$OWNERSHIP_MANIFEST")"
manifest_tenant_id="$(jq -r '.tenantId // empty' "$OWNERSHIP_MANIFEST")"
manifest_workspace_name="$(jq -r '.workspaceName // empty' "$OWNERSHIP_MANIFEST")"
manifest_fixture="$(jq -r '.fixture // empty' "$OWNERSHIP_MANIFEST")"
manifest_owner="$(jq -r '.managedBy // empty' "$OWNERSHIP_MANIFEST")"
[ "$manifest_workspace_id" = "$WORKSPACE_ID" ] || fail 'ownership manifest workspaceId does not match'
[ "$manifest_tenant_id" = "$FABRIC_ONTOLOGY_TENANT_ID" ] || fail 'ownership manifest tenantId does not match'
[ "$manifest_workspace_name" = "$EXPECTED_NAME" ] || fail 'ownership manifest workspaceName does not match'
[ "$manifest_fixture" = 'lamna-healthcare' ] || fail 'ownership manifest fixture is not lamna-healthcare'
[ "$manifest_owner" = 'enterprise-data-analyst' ] || fail 'ownership manifest managedBy does not match'

access_token="$(az account get-access-token --tenant "$FABRIC_ONTOLOGY_TENANT_ID" \
    --resource 'https://api.fabric.microsoft.com' --query accessToken --output tsv --only-show-errors)" \
    || fail 'unable to acquire a Fabric token for the configured tenant'
[ -n "$access_token" ] || fail 'Fabric token was empty'

response_file="$(mktemp)"
trap 'rm -f "$response_file"' EXIT HUP INT TERM
workspace_url="$API_ROOT/workspaces/$WORKSPACE_ID"
get_status="$(curl --silent --show-error -o "$response_file" --write-out '%{http_code}' \
    --header "Authorization: Bearer $access_token" "$workspace_url")" || fail 'unable to point-read Fabric workspace'
[ "$get_status" = '200' ] || fail "Fabric workspace point-read returned HTTP $get_status"

actual_workspace_id="$(jq -r '.id // empty' "$response_file")"
actual_workspace_name="$(jq -r '.displayName // empty' "$response_file")"
actual_tenant_id="$(jq -r '.tenantId // empty' "$response_file")"
[ "$actual_workspace_id" = "$WORKSPACE_ID" ] || fail 'Fabric workspace ID does not match'
[ "$actual_workspace_name" = "$EXPECTED_NAME" ] || fail 'Fabric workspace display name does not match'
if [ -n "$actual_tenant_id" ]; then
    [ "$actual_tenant_id" = "$FABRIC_ONTOLOGY_TENANT_ID" ] || fail 'Fabric workspace tenant does not match'
fi

delete_status="$(curl --silent --show-error -X DELETE -o "$response_file" --write-out '%{http_code}' \
    --header "Authorization: Bearer $access_token" "$workspace_url")" || fail 'Fabric workspace deletion request failed'
case "$delete_status" in
    200 | 202 | 204) ;;
    *) fail "Fabric workspace deletion returned HTTP $delete_status" ;;
esac

deadline=$(( $(date +%s) + 60 ))
while [ "$(date +%s)" -lt "$deadline" ]; do
    poll_status="$(curl --silent --show-error -o "$response_file" --write-out '%{http_code}' \
        --header "Authorization: Bearer $access_token" "$workspace_url")" || fail 'unable to verify workspace deletion'
    [ "$poll_status" = '404' ] && break
    sleep 2
done
[ "${poll_status:-}" = '404' ] || fail 'workspace was still present after 60 seconds'

printf '%s\n' "Deleted Fabric workspace $WORKSPACE_ID. Capacity, resource groups, B2B users, and Entra applications were not changed."