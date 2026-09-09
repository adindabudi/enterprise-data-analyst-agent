#!/usr/bin/env bash
set -euo pipefail

fail() {
  printf 'FAIL: %s\n' "$1" >&2
  exit 1
}

require_version() {
  command -v "$1" >/dev/null 2>&1 || fail "required command is unavailable: $1"
  "$1" version 2>/dev/null | grep -q "$2" || fail "required $1 version is $2"
}

require_version grype '0.116.0'
require_version trivy '0.72.0'
command -v jq >/dev/null 2>&1 || fail "required command is unavailable: jq"
: "${EDA_API_IMAGE:?EDA_API_IMAGE is required}"
: "${EDA_WORKER_IMAGE:?EDA_WORKER_IMAGE is required}"
: "${EDA_SANDBOX_IMAGE:?EDA_SANDBOX_IMAGE is required}"

waiver_file="docs/security/vulnerability-waivers.json"
sbom_dir="${SBOM_DIR:-.artifacts/sbom}"
tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT HUP INT TERM

for image_var in EDA_API_IMAGE EDA_WORKER_IMAGE EDA_SANDBOX_IMAGE; do
  image="${!image_var}"
  case "$image" in
    *@sha256:????????????????????????????????????????????????????????????????) ;;
    *) fail "${image_var} must be pinned by digest" ;;
  esac
done

python_script='from pathlib import Path
import json
import sys
from supply_chain_policy import load_waivers, normalize_vulnerability_findings, validate_vulnerabilities

waivers = load_waivers(Path(sys.argv[1]))
documents = [json.loads(Path(path).read_text(encoding="utf-8")) for path in sys.argv[2:]]
validate_vulnerabilities(normalize_vulnerability_findings(documents), waivers, today=sys.argv[0])'

index=0
for image_entry in \
  "$EDA_API_IMAGE:eda-api-image" \
  "$EDA_WORKER_IMAGE:eda-worker-image" \
  "$EDA_SANDBOX_IMAGE:eda-sandbox-image"; do
  image="${image_entry%:*}"
  sbom_name="${image_entry##*:}"
  sbom_file="$sbom_dir/${sbom_name}.cdx.json"
  [[ -f "$sbom_file" ]] || fail "required image SBOM is missing: $sbom_file"
  grype_output="$tmp_dir/grype-$index.json"
  trivy_output="$tmp_dir/trivy-$index.json"
  grype "sbom:$sbom_file" --output json >"$grype_output" || fail "grype SBOM scan failed for $image"
  trivy image --format json --severity HIGH,CRITICAL "$image" >"$trivy_output" || fail "trivy scan failed for $image"
  today="${SUPPLY_CHAIN_TODAY:-$(date +%F)}"
  PYTHONPATH="scripts${PYTHONPATH:+:$PYTHONPATH}" python3 -c "$python_script" "$today" "$waiver_file" "$grype_output" "$trivy_output" \
    || fail "unwaived vulnerability findings for $image"
  index=$((index + 1))
done
printf 'PASS: vulnerability scanners reported no unwaived high or critical findings\n'
