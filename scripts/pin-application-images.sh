#!/bin/sh
set -eu

fail() {
    printf '%s\n' "FAIL: $1" >&2
    exit 1
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || fail "required command is unavailable: $1"
}

for command_name in az azd jq sha256sum; do
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

azd_optional_value() {
    variable_name="$1"
    variable_value="$(printf '%s\n' "$azd_values" | sed -n "s/^${variable_name}=//p" | head -n 1)"
    case "$variable_value" in
        \"*\") variable_value="${variable_value#\"}"; variable_value="${variable_value%\"}" ;;
    esac
    printf '%s\n' "$variable_value"
}

resource_name() {
    resource_id="$1"
    case "$resource_id" in
        /subscriptions/*/resourceGroups/*/providers/Microsoft.App/*/*) printf '%s\n' "${resource_id##*/}" ;;
        *) fail "invalid Container Apps resource ID" ;;
    esac
}

environment_name="$(azd_value AZURE_ENV_NAME)"
resource_group="$(azd_value AZURE_RESOURCE_GROUP)"
registry_login_server="$(azd_value CONTAINER_REGISTRY_LOGIN_SERVER)"
api_app_id="$(azd_value API_APP_ID)"
cleanup_job_id="$(azd_value CLEANUP_JOB_ID)"
fabric_acceptance_job_id="$(azd_optional_value FABRIC_ACCEPTANCE_JOB_ID)"
case "$environment_name" in
    *[!a-z0-9-]* | '') fail "AZURE_ENV_NAME must contain only lowercase letters, digits, and hyphens" ;;
esac
case "$registry_login_server" in
    *.azurecr.io) ;;
    *) fail "CONTAINER_REGISTRY_LOGIN_SERVER must be an Azure Container Registry login server" ;;
esac

registry_name="${registry_login_server%%.*}"
api_name="$(resource_name "$api_app_id")"
cleanup_name="$(resource_name "$cleanup_job_id")"
fabric_acceptance_name=""
if [ -n "$fabric_acceptance_job_id" ]; then
    fabric_acceptance_name="$(resource_name "$fabric_acceptance_job_id")"
fi

resolve_digest() {
    source_image="$1"
    case "$source_image" in
        "${registry_login_server}"/*@sha256:????????????????????????????????????????????????????????????????)
            digest="${source_image##*@}"
            resolved_image="$source_image"
            return
            ;;
        "${registry_login_server}"/*:*) ;;
        *) fail "deployed image must be an ACR tag from ${registry_login_server}" ;;
    esac
    image_path="${source_image#${registry_login_server}/}"
    case "$image_path" in
        *@* | *: | :*) fail "deployed image must contain a mutable repository tag" ;;
    esac
    repository="${image_path%:*}"
    tag="${image_path##*:}"
    [ "$repository" != "$image_path" ] || fail "deployed image must contain a repository tag"
    digest="$(az acr manifest show-metadata \
        --registry "$registry_name" \
        --name "${repository}:${tag}" \
        --query digest \
        --output tsv \
        --only-show-errors)" || fail "unable to resolve ACR digest for ${repository}:${tag}"
    case "$digest" in
        sha256:????????????????????????????????????????????????????????????????) ;;
        *) fail "ACR did not return a SHA-256 digest for ${repository}:${tag}" ;;
    esac
    resolved_image="${registry_login_server}/${repository}@${digest}"
}

wait_for_revision() {
    app_name="$1"
    attempts=0
    while [ "$attempts" -lt 72 ]; do
        revision="$(az containerapp show --name "$app_name" --resource-group "$resource_group" \
            --query properties.latestRevisionName --output tsv --only-show-errors)" || fail "unable to read ${app_name} revision"
        states="$(az containerapp revision show --name "$app_name" --resource-group "$resource_group" --revision "$revision" \
            --query '{provisioning: properties.provisioningState, health: properties.healthState}' \
            --output json --only-show-errors)" \
            || fail "unable to read ${app_name} revision state"
        provisioning_state="$(printf '%s' "$states" | jq -r '.provisioning // empty')"
        health_state="$(printf '%s' "$states" | jq -r '.health // empty')"
        [ "$provisioning_state" = "Provisioned" ] && [ "$health_state" = "Healthy" ] && return
        attempts=$((attempts + 1))
        sleep 5
    done
    fail "${app_name} did not become healthy"
}

api_source_image="$(az containerapp show --ids "$api_app_id" --query properties.template.containers[0].image --output tsv --only-show-errors)" \
    || fail "unable to read API image"
resolve_digest "$api_source_image"
api_digest="$digest"
api_image="$resolved_image"
worker_image="$(azd_value EDA_WORKER_IMAGE)"
case "$worker_image" in
    "${registry_login_server}"/*@sha256:????????????????????????????????????????????????????????????????) ;;
    *) fail "prebuilt worker image must be an immutable image from ${registry_login_server}" ;;
esac
worker_digest="${worker_image##*@}"

az containerapp job update --name "$cleanup_name" --resource-group "$resource_group" --image "$worker_image" --only-show-errors >/dev/null \
    || fail "unable to pin cleanup job image"
if [ -n "$fabric_acceptance_name" ]; then
    az containerapp job update --name "$fabric_acceptance_name" --resource-group "$resource_group" --image "$worker_image" --only-show-errors >/dev/null \
        || fail "unable to pin Fabric acceptance job image"
fi
az containerapp update --name "$api_name" --resource-group "$resource_group" \
    --image "$api_image" \
    --set-env-vars \
    EDA_WORKER_IMAGE_DIGEST="$worker_digest" \
    EDA_MODEL_CONTRACT_VERIFIED=true \
    EDA_TOKENIZER_CALIBRATED=true \
    EDA_HOSTED_AGENT_ENABLED=true \
    EDA_ENTRA_FEDERATION_READY=true \
    EDA_CORE_READY=true \
    --only-show-errors >/dev/null \
    || fail "unable to pin and promote API image"
wait_for_revision "$api_name"

final_api_image="$(az containerapp show --ids "$api_app_id" --query properties.template.containers[0].image --output tsv --only-show-errors)" \
    || fail "unable to verify API image"
final_cleanup_image="$(az containerapp job show --ids "$cleanup_job_id" --query properties.template.containers[0].image --output tsv --only-show-errors)" \
    || fail "unable to verify cleanup job image"
final_fabric_acceptance_image="$worker_image"
if [ -n "$fabric_acceptance_job_id" ]; then
    final_fabric_acceptance_image="$(az containerapp job show --ids "$fabric_acceptance_job_id" --query properties.template.containers[0].image --output tsv --only-show-errors)" \
        || fail "unable to verify Fabric acceptance job image"
fi
for final_image in "$final_api_image" "$final_cleanup_image" "$final_fabric_acceptance_image"; do
    case "$final_image" in
        *@sha256:????????????????????????????????????????????????????????????????) ;;
        *) fail "final deployed image is not pinned by SHA-256 digest" ;;
    esac
done
[ "$final_api_image" = "$api_image" ] || fail "API image changed while pinning"
[ "$final_cleanup_image" = "$worker_image" ] || fail "cleanup image differs from worker image"
[ "$final_fabric_acceptance_image" = "$worker_image" ] || fail "Fabric acceptance image differs from worker image"

verify_api_environment() {
    variable_name="$1"
    expected_value="$2"
    actual_value="$(az containerapp show --ids "$api_app_id" \
        --query "properties.template.containers[0].env[?name=='${variable_name}'].value | [0]" \
        --output tsv --only-show-errors)" || fail "unable to read API environment ${variable_name}"
    [ "$actual_value" = "$expected_value" ] || fail "API environment ${variable_name} does not match"
}

verify_api_environment 'EDA_WORKER_IMAGE_DIGEST' "$worker_digest"
verify_api_environment 'EDA_MODEL_CONTRACT_VERIFIED' 'true'
verify_api_environment 'EDA_TOKENIZER_CALIBRATED' 'true'
verify_api_environment 'EDA_HOSTED_AGENT_ENABLED' 'true'
verify_api_environment 'EDA_ENTRA_FEDERATION_READY' 'true'
verify_api_environment 'EDA_CORE_READY' 'true'

artifact_directory=".azure/${environment_name}"
mkdir -p "$artifact_directory"
temporary_manifest="$(mktemp "${artifact_directory}/images.json.XXXXXX")" || fail "unable to create image manifest"
trap 'rm -f "$temporary_manifest"' EXIT HUP INT TERM
jq -n \
    --arg environment "$environment_name" \
    --arg api_image "$api_image" \
    --arg api_digest "$api_digest" \
    --arg worker_image "$worker_image" \
    --arg worker_digest "$worker_digest" \
    '{environment: $environment, api: {image: $api_image, digest: $api_digest}, worker: {image: $worker_image, digest: $worker_digest}}' \
    > "$temporary_manifest" || fail "unable to write image manifest"
mv "$temporary_manifest" "${artifact_directory}/images.json"
azd env set EDA_API_IMAGE "$api_image" >/dev/null || fail "unable to persist API image metadata"
azd env set EDA_WORKER_IMAGE "$worker_image" >/dev/null || fail "unable to persist worker image metadata"

printf '%s\n' "application images pinned for ${environment_name}"