#!/usr/bin/env bash
set -euo pipefail

require_step() {
  local command_path="$1"
  [[ -n "$command_path" ]] || {
    printf 'FAIL: gate step command path is empty\n' >&2
    exit 1
  }
  [[ -x "$command_path" ]] || {
    printf 'FAIL: gate step is not executable: %s\n' "$command_path" >&2
    exit 1
  }
}

generate_sbom_cmd="${GENERATE_SBOM_CMD:-./scripts/generate-sbom.sh}"
verify_license_cmd="${VERIFY_LICENSE_CMD:-}"
scan_vulnerabilities_cmd="${SCAN_VULNERABILITIES_CMD:-./scripts/scan-vulnerabilities.sh}"
scan_secrets_cmd="${SCAN_SECRETS_CMD:-./scripts/scan-secrets.sh}"
sign_images_cmd="${SIGN_IMAGES_CMD:-./scripts/sign-images.sh}"

if [[ -n "$verify_license_cmd" ]]; then
  require_step "$verify_license_cmd"
fi
require_step "$generate_sbom_cmd"
require_step "$scan_vulnerabilities_cmd"
require_step "$scan_secrets_cmd"
require_step "$sign_images_cmd"

"$generate_sbom_cmd"
if [[ -n "$verify_license_cmd" ]]; then
  "$verify_license_cmd"
else
  uv run python scripts/verify_license_allowlist.py \
    --sbom-dir .artifacts/sbom \
    --exclude-sbom uv-lock.cdx.json \
    --overrides docs/security/license-overrides.json
fi
"$scan_vulnerabilities_cmd"
"$scan_secrets_cmd"
"$sign_images_cmd"

printf 'PASS: supply chain gate completed\n'