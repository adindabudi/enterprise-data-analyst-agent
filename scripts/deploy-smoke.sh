#!/bin/sh
set -eu

fail() {
    printf '%s\n' "FAIL: $1" >&2
    exit 1
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || fail "required command is unavailable: $1"
}

for command_name in az azd curl jq make sha256sum uv npx; do
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
resource_group="$(azd_value AZURE_RESOURCE_GROUP)"
api_app_id="$(azd_value API_APP_ID)"
app_url="${EDA_APP_URL:-$(azd_value API_URL)}"
case "$app_url" in
    https://*) ;;
    *) fail "EDA_APP_URL or API_URL must be an HTTPS URL" ;;
esac
[ -f ".azure/${environment_name}/images.json" ] || fail "application image manifest is missing"

artifact_directory=".artifacts"
mkdir -p "$artifact_directory"
run_id="$(date -u +%Y%m%dT%H%M%SZ)-$$"
records_file="$(mktemp "${artifact_directory}/deploy-smoke.${run_id}.XXXXXX")" || fail "unable to create smoke status file"
manifest_file="${artifact_directory}/deploy-smoke-${run_id}.json"
trap 'rm -f "$records_file"' EXIT HUP INT TERM

run_gate() {
    gate_name="$1"
    shift
    gate_output="$(mktemp "${artifact_directory}/deploy-smoke-output.XXXXXX")" || fail "unable to create gate output file"
    if "$@" >"$gate_output" 2>&1; then
        status="passed"
    else
        status="failed"
    fi
    command_hash="$(printf '%s' "$gate_name" | sha256sum | awk '{print $1}')"
    printf '%s|%s|%s\n' "$gate_name" "$status" "$command_hash" >> "$records_file"
    rm -f "$gate_output"
    [ "$status" = "passed" ] || fail "deployment gate failed: ${gate_name}"
}

analysis_runtime_ready() {
    health_state="$(curl --fail --silent --show-error "${app_url%/}/health/ready" | jq -r '.components.analysisRuntime // empty')" \
        || return 1
    [ "$health_state" = "ready" ]
}

document_pack_ready() {
    documents_enabled="${DOCUMENTS_ENABLED:-false}"
    case "$documents_enabled" in
        true | false) ;;
        *) return 1 ;;
    esac
    health_state="$(curl --fail --silent --show-error "${app_url%/}/health/ready" | jq -r '.featurePacks.documents // empty')" \
        || return 1
    if [ "$documents_enabled" = "false" ]; then
        [ "$health_state" = "disabled" ]
        return
    fi
    case "$health_state" in
        configured | ready) ;;
        *) return 1 ;;
    esac
    expected_image="$(jq -r '.worker.image // empty' ".azure/${environment_name}/images.json")"
    expected_worker_digest="$(jq -r '.worker.digest // empty' ".azure/${environment_name}/images.json")"
    sandbox_image="$(jq -r '.image // empty' ".azure/${environment_name}/sandbox-image.json")"
    expected_sandbox_digest="$(jq -r '.digest // empty' ".azure/${environment_name}/sandbox-image.json")"
    [ -n "$expected_worker_digest" ] || return 1
    [ -n "$expected_sandbox_digest" ] || return 1
    EDA_WORKER_IMAGE="$expected_image" EDA_WORKER_IMAGE_DIGEST="$expected_worker_digest" \
        EDA_SANDBOX_IMAGE="$sandbox_image" EDA_SANDBOX_IMAGE_DIGEST="$expected_sandbox_digest" DOCUMENTS_ENABLED=true \
        ./scripts/doctor-documents.sh --phase postdeploy >/dev/null
}

write_rbac_outputs() {
    jq -n \
        --arg storage "$(azd_value STORAGE_ACCOUNT_ID)" \
        --arg cosmos "$(azd_value COSMOS_ACCOUNT_ID)" \
        --arg redis "$(azd_value REDIS_CLUSTER_ID)" \
        --arg sandbox "/subscriptions/$(azd_value AZURE_SUBSCRIPTION_ID)/resourceGroups/$(azd_value EDA_SANDBOX_RESOURCE_GROUP)/providers/Microsoft.App/sandboxGroups/$(azd_value EDA_SANDBOX_GROUP)" \
        --arg web "$(azd_value WEB_IDENTITY_PRINCIPAL_ID)" \
        --arg worker "$(azd_value WORKER_IDENTITY_PRINCIPAL_ID)" \
        '{storageAccountId: {value: $storage}, cosmosAccountId: {value: $cosmos}, redisClusterId: {value: $redis}, sandboxGroupId: {value: $sandbox}, webIdentityPrincipalId: {value: $web}, workerIdentityPrincipalId: {value: $worker}}'
}

run_gate api-readiness curl --fail --silent --show-error "${app_url%/}/health/ready"
run_gate analysis-runtime-readiness analysis_runtime_ready
run_gate document-pack-intent document_pack_ready
run_gate entra-federation python scripts/test-entra-app.py
rbac_outputs="$(mktemp "${artifact_directory}/rbac-outputs.XXXXXX")" || fail "unable to create RBAC outputs"
trap 'rm -f "$records_file" "$rbac_outputs"' EXIT HUP INT TERM
write_rbac_outputs > "$rbac_outputs" || fail "unable to construct RBAC outputs"
run_gate rbac-inventory python scripts/verify-rbac.py --outputs "$rbac_outputs"
run_gate deployed-storage-gate make deployed-storage-gate
run_gate model-contract make model-contract
run_gate sandbox-benchmark make sandbox-benchmark
run_gate acceptance-core make acceptance-core
run_gate playwright npx playwright test --project=desktop --project=mobile tests/e2e/workspace.spec.ts tests/e2e/accessibility.spec.ts
: "${EDA_TELEMETRY_CORRELATION_ID:?FAIL: EDA_TELEMETRY_CORRELATION_ID must identify the synthetic acceptance task}"
: "${EDA_TELEMETRY_SENTINELS_JSON:?FAIL: EDA_TELEMETRY_SENTINELS_JSON must contain telemetry leak sentinels}"
run_gate telemetry-leak-scan uv run pytest tests/deployment/test_telemetry_leaks.py -q

jq -Rn --arg run_id "$run_id" --arg environment "$environment_name" \
    '[inputs | split("|") | {gate: .[0], status: .[1], commandHash: .[2]}] | {runId: $run_id, environment: $environment, gates: ., status: "passed"}' \
    < "$records_file" > "$manifest_file" || fail "unable to write sanitized smoke manifest"
printf '%s\n' "deploy smoke passed: run=${run_id} status=passed"