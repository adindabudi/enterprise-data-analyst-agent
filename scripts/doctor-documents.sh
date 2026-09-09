#!/usr/bin/env bash
set -euo pipefail

fail() {
    printf '%s\n' "FAIL: $1" >&2
    exit 1
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || fail "required command is unavailable: $1"
}

phase=""
output=""
while [[ "$#" -gt 0 ]]; do
    case "$1" in
        --phase)
            phase="${2:-}"
            shift 2
            ;;
        --output)
            output="${2:-}"
            shift 2
            ;;
        --help)
            printf '%s\n' "Usage: doctor-documents.sh --phase prebuild|postdeploy [--output PATH]"
            exit 0
            ;;
        *) fail "unsupported argument: $1" ;;
    esac
done
[[ "$phase" == "prebuild" || "$phase" == "postdeploy" ]] \
    || fail "usage: doctor-documents.sh --phase prebuild|postdeploy"

documents_enabled="${DOCUMENTS_ENABLED:-false}"
[[ "$documents_enabled" == "true" || "$documents_enabled" == "false" ]] \
    || fail "DOCUMENTS_ENABLED must be true or false"

require_command python3
require_command sha256sum
[[ -r skills.lock.json ]] || fail "skills.lock.json is unavailable"
expected_hash="$(sha256sum skills.lock.json | cut -d' ' -f1)"
if [[ "$documents_enabled" == "true" ]]; then
    [[ "${EDA_DOCUMENT_TERMS_ACCEPTED:-}" == "$expected_hash" ]] \
        || fail "EDA_DOCUMENT_TERMS_ACCEPTED does not match the reviewed skills.lock.json"
fi

if [[ "$phase" == "prebuild" ]]; then
    if [[ "$documents_enabled" == "false" ]]; then
        printf '%s\n' "SKIP: Document Pack intentionally disabled"
        exit 0
    fi
    printf '%s\n' "PASS: Document Pack terms_sha256=${expected_hash}"
    exit 0
fi

require_command docker
require_command tar
[[ -n "${EDA_WORKER_IMAGE:-}" ]] || fail "EDA_WORKER_IMAGE is required after deployment"
[[ -n "${EDA_SANDBOX_IMAGE:-}" ]] || fail "EDA_SANDBOX_IMAGE is required after deployment"
for image in "$EDA_WORKER_IMAGE" "$EDA_SANDBOX_IMAGE"; do
    case "$image" in
        *@sha256:????????????????????????????????????????????????????????????????) ;;
        *) fail "Document Pack images must be pinned by SHA-256 digest" ;;
    esac
done

lock_commit="$(python3 -c 'import json; print(json.load(open("skills.lock.json", encoding="utf-8"))["commit"])')"
document_skills="$(python3 -c 'import json; names = {'\''docx'\'', '\''pdf'\'', '\''pptx'\'', '\''xlsx'\''}; print(" ".join(sorted(entry["name"] for entry in json.load(open("skills.lock.json", encoding="utf-8"))["skills"] if entry["name"] in names)))')"
web_hashes="$(python3 -c 'import json; entry = next(value for value in json.load(open("skills.lock.json", encoding="utf-8"))["skills"] if value["name"] == "web-artifacts-builder"); print(entry["skillMdSha256"], entry["licenseSha256"])')"
read -r web_skill_md_sha256 web_license_sha256 <<<"$web_hashes"
[[ -n "$document_skills" ]] || fail "skills.lock.json declares no document skills"
cleanup_image_inspection() {
    local container_id="$1"
    local extract_root="$2"
    if [[ -n "$container_id" ]]; then
        docker rm --force "$container_id" >/dev/null 2>&1 || true
    fi
    if [[ -d "$extract_root" ]]; then
        chmod -R u+rwX "$extract_root" >/dev/null 2>&1 || true
        rm -rf -- "$extract_root"
    fi
}

inspect_image() {
    local image="$1"
    local container_id=""
    local extract_root=""
    local metadata=""
    docker pull --platform linux/amd64 "$image" >/dev/null || return 1
    extract_root="$(mktemp -d)" || return 1
    chmod 700 "$extract_root"
    container_id="$(docker create --platform linux/amd64 --entrypoint /bin/true "$image")" || {
        cleanup_image_inspection "$container_id" "$extract_root"
        return 1
    }
    if ! docker cp "${container_id}:/opt/document-skills" - \
        | tar --extract --directory "$extract_root" --no-same-owner --no-same-permissions \
        || ! docker cp "${container_id}:/opt/web-skills/web-artifacts-builder" - \
            | tar --extract --directory "$extract_root" --no-same-owner --no-same-permissions; then
        cleanup_image_inspection "$container_id" "$extract_root"
        return 1
    fi
    metadata="$(python3 - "$extract_root/document-skills" "$extract_root/web-artifacts-builder" \
        "$lock_commit" "$document_skills" "$documents_enabled" "$web_skill_md_sha256" "$web_license_sha256" <<'PY'
import hashlib
import json
from pathlib import Path
import sys

root = Path(sys.argv[1])
web_root = Path(sys.argv[2])
expected_commit = sys.argv[3]
expected_documents = sys.argv[4].split()
documents_enabled = sys.argv[5] == "true"
expected_web_skill_md_sha256 = sys.argv[6]
expected_web_license_sha256 = sys.argv[7]
required_web_paths = (
    "SKILL.md",
    "LICENSE.txt",
    ".acquired.json",
    "scripts/init-artifact.sh",
    "scripts/bundle-artifact.sh",
    "scripts/shadcn-components.tar.gz",
)
for relative in required_web_paths:
    candidate = web_root / relative
    if not candidate.is_file() or candidate.is_symlink():
        raise SystemExit(1)
web_record = json.loads((web_root / ".acquired.json").read_text(encoding="utf-8"))
if web_record.get("commit") != expected_commit:
    raise SystemExit(1)
if web_record.get("skillMdSha256") != expected_web_skill_md_sha256:
    raise SystemExit(1)
if web_record.get("licenseSha256") != expected_web_license_sha256:
    raise SystemExit(1)
web_identity = {
    "commit": web_record["commit"],
    "skillMdSha256": web_record["skillMdSha256"],
    "licenseSha256": web_record["licenseSha256"],
}
bundles = {}
if documents_enabled:
    for name in expected_documents:
        directory = root / name
        bundle = root / f"{name}.zip"
        metadata = directory / ".acquired.json"
        if not directory.is_dir() or not bundle.is_file() or not metadata.is_file():
            raise SystemExit(1)
        record = json.loads(metadata.read_text(encoding="utf-8"))
        if record.get("commit") != expected_commit:
            raise SystemExit(1)
        digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
        if len(digest) != 64:
            raise SystemExit(1)
        bundles[name] = digest
print(json.dumps({"bundles": bundles, "commit": expected_commit, "web": web_identity}, sort_keys=True, separators=(",", ":")))
PY
)" || {
        cleanup_image_inspection "$container_id" "$extract_root"
        return 1
    }
    cleanup_image_inspection "$container_id" "$extract_root"
    printf '%s\n' "$metadata"
}
worker_metadata="$(inspect_image "$EDA_WORKER_IMAGE")" || fail "deployed worker skill metadata verification failed"
sandbox_metadata="$(inspect_image "$EDA_SANDBOX_IMAGE")" || fail "deployed sandbox skill metadata verification failed"
[[ "$worker_metadata" == "$sandbox_metadata" ]] || fail "worker and sandbox skill packs differ"

if [[ "$documents_enabled" == "false" ]]; then
    printf '%s\n' "PASS: mandatory Web Artifact Pack verified; Document Pack intentionally disabled"
    exit 0
fi

if [[ -n "$output" ]]; then
    [[ -n "${EDA_DEPLOYMENT_ID:-}" ]] || fail "EDA_DEPLOYMENT_ID is required when writing an image contract"
    worker_digest="${EDA_WORKER_IMAGE##*@}"
    sandbox_digest="${EDA_SANDBOX_IMAGE##*@}"
    python3 - "$worker_metadata" "$EDA_DEPLOYMENT_ID" "$worker_digest" "$sandbox_digest" "$expected_hash" "$output" <<'PY' \
        || fail "could not write the Document Pack image contract"
import json
import os
import sys
from pathlib import Path

metadata_raw, deployment_id, worker_digest, sandbox_digest, lock_sha256, output_value = sys.argv[1:]
metadata = json.loads(metadata_raw)
if set(metadata) != {"bundles", "commit", "web"}:
    raise SystemExit(1)
contract = {
    "schemaVersion": 1,
    "deploymentId": deployment_id,
    "workerImageDigest": worker_digest,
    "sandboxImageDigest": sandbox_digest,
    "lockSha256": lock_sha256,
    "commit": metadata["commit"],
    "bundles": metadata["bundles"],
}
output_path = Path(output_value)
output_path.parent.mkdir(parents=True, exist_ok=True)
temporary = output_path.with_name(f".{output_path.name}.{os.getpid()}.tmp")
try:
    temporary.write_text(json.dumps(contract, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(output_path)
finally:
    temporary.unlink(missing_ok=True)
PY
fi

printf '%s\n' "PASS: Document Pack worker and sandbox image metadata verified"
