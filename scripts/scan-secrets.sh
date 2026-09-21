#!/usr/bin/env bash
set -euo pipefail

fail() {
  printf 'FAIL: %s\n' "$1" >&2
  exit 1
}

command -v gitleaks >/dev/null 2>&1 || {
  printf 'FAIL: required command is unavailable: gitleaks\n' >&2
  exit 1
}
gitleaks version | grep -q '8.30.1' || {
  printf 'FAIL: required gitleaks version is 8.30.1\n' >&2
  exit 1
}
configuration="$PWD/.gitleaks.toml"
gitleaks git --config "$configuration" --no-banner --redact=100 --exit-code 1 . \
  || fail "gitleaks detected a secret in git history"
snapshot="$(mktemp -d "${TMPDIR:-/tmp}/eda-secret-scan.XXXXXX")"
trap 'rm -rf "$snapshot"' EXIT
git ls-files --cached --others --exclude-standard -z \
  | while IFS= read -r -d '' source; do
      if [[ -f "$source" || -L "$source" ]]; then
        printf '%s\0' "$source"
      fi
    done \
  | tar --no-recursion --null -cf - --files-from=- \
  | tar -xf - -C "$snapshot"
gitleaks dir --config "$configuration" --no-banner --redact=100 --exit-code 1 "$snapshot" \
  || fail "gitleaks detected a secret in publishable working-tree files"
printf 'PASS: gitleaks found no secrets\n'
