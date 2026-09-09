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
gitleaks git --config .gitleaks.toml --no-banner --exit-code 1 . || fail "gitleaks detected a secret in git history"
gitleaks dir --config .gitleaks.toml --no-banner --exit-code 1 . || fail "gitleaks detected a secret in the working tree"
printf 'PASS: gitleaks found no secrets\n'
