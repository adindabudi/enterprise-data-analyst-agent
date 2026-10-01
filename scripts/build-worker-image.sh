#!/bin/sh
set -eu

fail() {
    printf '%s\n' "FAIL: $1" >&2
    exit 1
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || fail "required command is unavailable: $1"
}

for command_name in az azd git jq syft trivy; do
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

environment_name="$(azd_value AZURE_ENV_NAME)"
registry_login_server="$(azd_value CONTAINER_REGISTRY_LOGIN_SERVER)"
registry_resource_id="$(azd_optional_value CONTAINER_REGISTRY_ID)"
case "$environment_name" in
    *[!a-z0-9-]* | '') fail "AZURE_ENV_NAME must contain only lowercase letters, digits, and hyphens" ;;
esac
case "$registry_login_server" in
    *.azurecr.io) ;;
    *) fail "CONTAINER_REGISTRY_LOGIN_SERVER must be an Azure Container Registry login server" ;;
esac

repository="enterprise-data-analyst/eda-worker"
registry_name="${registry_login_server%%.*}"
registry_role_assignment_mode="$(az acr show \
    --name "$registry_name" \
    --query roleAssignmentMode \
    --output tsv \
    --only-show-errors)" || fail "unable to inspect the registry role assignment mode"
source_revision="$(git rev-parse --verify HEAD)" || fail "unable to resolve the worker source revision"
image_tag="$source_revision"
project_root="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"

documents_enabled="${DOCUMENTS_ENABLED:-$(azd_optional_value DOCUMENTS_ENABLED)}"
documents_enabled="${documents_enabled:-false}"
document_terms_accepted=""
case "$documents_enabled" in
    true)
        document_terms_accepted="${EDA_DOCUMENT_TERMS_ACCEPTED:-$(azd_optional_value EDA_DOCUMENT_TERMS_ACCEPTED)}"
        [ -n "$document_terms_accepted" ] || fail "EDA_DOCUMENT_TERMS_ACCEPTED is required when documents are enabled"
        ;;
    false) ;;
    *) fail "DOCUMENTS_ENABLED must be true or false" ;;
esac

case "$registry_role_assignment_mode" in
    AbacRepositoryPermissions)
        az acr build \
            --registry "$registry_name" \
            --image "${repository}:${image_tag}" \
            --platform "linux/amd64" \
            --source-acr-auth-id "[caller]" \
            --file "$project_root/services/worker/Dockerfile" \
            --build-arg "SOURCE_REVISION=${source_revision}" \
            --build-arg "EDA_DOCUMENT_TERMS_ACCEPTED=${document_terms_accepted}" \
            "$project_root" \
            --only-show-errors || fail "worker image build failed"
        ;;
    LegacyRegistryPermissions)
        az acr build \
            --registry "$registry_name" \
            --image "${repository}:${image_tag}" \
            --platform "linux/amd64" \
            --file "$project_root/services/worker/Dockerfile" \
            --build-arg "SOURCE_REVISION=${source_revision}" \
            --build-arg "EDA_DOCUMENT_TERMS_ACCEPTED=${document_terms_accepted}" \
            "$project_root" \
            --only-show-errors || fail "worker image build failed"
        ;;
    *) fail "unsupported registry role assignment mode" ;;
esac

digest="$(az acr manifest show-metadata \
    --registry "$registry_name" \
    --name "${repository}:${image_tag}" \
    --query digest \
    --output tsv \
    --only-show-errors)" || fail "unable to resolve the worker image digest"
case "$digest" in
    sha256:????????????????????????????????????????????????????????????????) ;;
    *) fail "ACR did not return a valid worker SHA-256 image digest" ;;
esac
image="${registry_login_server}/${repository}@${digest}"

az acr login --name "$registry_name" --only-show-errors || fail "unable to authenticate image scanners to ACR"
trivy image --image-src remote --platform linux/amd64 --timeout 60m \
    --exit-code 1 --severity HIGH,CRITICAL --ignore-unfixed "$image" >/dev/null \
    || fail "worker image vulnerability scan failed"

artifact_directory="$project_root/.azure/$environment_name"
mkdir -p "$artifact_directory"
sbom_file="$artifact_directory/worker-image.spdx.json"
temporary_sbom="$(mktemp "${sbom_file}.XXXXXX")" || fail "unable to create temporary worker SBOM"
temporary_manifest="$(mktemp "${artifact_directory}/worker-image.json.XXXXXX")" \
    || fail "unable to create temporary worker image manifest"
trap 'rm -f "$temporary_sbom" "$temporary_manifest"' EXIT HUP INT TERM
syft "$image" --output spdx-json > "$temporary_sbom" || fail "worker image SBOM generation failed"
[ -s "$temporary_sbom" ] || fail "worker image SBOM is empty"
mv "$temporary_sbom" "$sbom_file"

jq -n \
    --arg digest "$digest" \
    --arg image "$image" \
    --arg repository "$repository" \
    --arg source_revision "$source_revision" \
    --arg sbom "$sbom_file" \
    '{digest: $digest, image: $image, repository: $repository, sourceRevision: $source_revision, sbom: $sbom}' \
    > "$temporary_manifest" || fail "unable to write worker image manifest"
mv "$temporary_manifest" "$artifact_directory/worker-image.json"

azd env set EDA_WORKER_IMAGE "$image" >/dev/null || fail "unable to persist worker image metadata"
azd env set EDA_WORKER_IMAGE_DIGEST "$digest" >/dev/null || fail "unable to persist worker digest metadata"
azd env set AZURE_CONTAINER_REGISTRY_ENDPOINT "$registry_login_server" >/dev/null \
    || fail "unable to persist the registry endpoint"
if [ -n "$registry_resource_id" ]; then
    azd env set AZURE_CONTAINER_REGISTRY_RESOURCE_ID "$registry_resource_id" >/dev/null \
        || fail "unable to persist the registry resource ID"
fi

printf '%s\n' "worker image digest captured for ${environment_name}"
