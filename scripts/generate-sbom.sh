#!/usr/bin/env bash
set -euo pipefail

fail() {
  printf 'FAIL: %s\n' "$1" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "required command is unavailable: $1"
}

require_command syft
require_command jq
require_command npm
version="$(syft version --output json | jq -r '.version // empty')"
[[ "$version" == "1.49.0" ]] || fail "Syft v1.49.0 is required, found ${version:-unknown}"

output_dir="${1:-.artifacts/sbom}"
mkdir -p "$output_dir"
for image_var in EDA_API_IMAGE EDA_WORKER_IMAGE EDA_SANDBOX_IMAGE; do
  image="${!image_var:?${image_var} is required}"
  case "$image" in
    *@sha256:????????????????????????????????????????????????????????????????) ;;
    *) fail "${image_var} must be pinned by digest" ;;
  esac
  name="$(printf '%s' "$image_var" | tr '[:upper:]' '[:lower:]' | tr '_' '-')"
  syft "$image" -o cyclonedx-json >"$output_dir/${name}.cdx.json" || fail "SBOM generation failed for ${image_var}"
  jq -e '.bomFormat == "CycloneDX"' "$output_dir/${name}.cdx.json" >/dev/null || fail "invalid CycloneDX output for ${image_var}"
done
for lockfile in uv.lock package-lock.json services/sandbox/package-lock.json; do
  [[ -f "$lockfile" ]] || fail "required lockfile is missing: $lockfile"
done

syft "file:uv.lock" -o cyclonedx-json >"$output_dir/uv-lock.cdx.json" || fail "SBOM generation failed for uv.lock"
npm sbom --omit=dev --package-lock-only --sbom-format cyclonedx >"$output_dir/root-package-lock.cdx.json" \
  || fail "SBOM generation failed for package-lock.json"
npm --prefix services/sandbox sbom --omit=dev --package-lock-only --sbom-format cyclonedx \
  >"$output_dir/sandbox-package-lock.cdx.json" \
  || fail "SBOM generation failed for services/sandbox/package-lock.json"

for sbom_file in uv-lock root-package-lock sandbox-package-lock; do
  jq -e '.bomFormat == "CycloneDX"' "$output_dir/${sbom_file}.cdx.json" >/dev/null \
    || fail "invalid CycloneDX output: ${sbom_file}"
done
printf 'PASS: generated CycloneDX SBOMs in %s\n' "$output_dir"
