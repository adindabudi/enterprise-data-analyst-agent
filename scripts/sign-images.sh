#!/usr/bin/env bash
set -euo pipefail

fail() {
  printf 'FAIL: %s\n' "$1" >&2
  exit 1
}

command -v cosign >/dev/null 2>&1 || {
  printf 'FAIL: required command is unavailable: cosign\n' >&2
  exit 1
}
cosign version | grep -q 'v3.1.2' || {
  printf 'FAIL: required cosign version is v3.1.2\n' >&2
  exit 1
}
PYTHONPATH="scripts${PYTHONPATH:+:$PYTHONPATH}" export PYTHONPATH
for image_var in EDA_API_IMAGE EDA_WORKER_IMAGE EDA_SANDBOX_IMAGE; do
  image="${!image_var:?${image_var} is required}"
  python3 - <<'PY' "$image" "$image_var"
from supply_chain_policy import SupplyChainPolicyError, validate_digest_image_ref
import sys

try:
    validate_digest_image_ref(sys.argv[1], label=sys.argv[2].lower())
except SupplyChainPolicyError as error:
    print(f"FAIL: {error}", file=sys.stderr)
    raise SystemExit(1)
PY
done
: "${COSIGN_CERTIFICATE_IDENTITY_REGEXP:?COSIGN_CERTIFICATE_IDENTITY_REGEXP is required}"
: "${COSIGN_CERTIFICATE_OIDC_ISSUER:?COSIGN_CERTIFICATE_OIDC_ISSUER is required}"
for image_var in EDA_API_IMAGE EDA_WORKER_IMAGE EDA_SANDBOX_IMAGE; do
  image="${!image_var}"
  cosign sign --yes "$image"
  cosign verify \
    --certificate-identity-regexp "$COSIGN_CERTIFICATE_IDENTITY_REGEXP" \
    --certificate-oidc-issuer "$COSIGN_CERTIFICATE_OIDC_ISSUER" \
    "$image" >/dev/null
done
printf 'PASS: images were signed and verified with cosign keyless signing\n'
