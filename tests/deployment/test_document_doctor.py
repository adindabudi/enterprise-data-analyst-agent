import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _parse_pinned_requirements(path: Path) -> dict[str, tuple[str, frozenset[str]]]:
    pinned: dict[str, tuple[str, frozenset[str]]] = {}
    name = ""
    version = ""
    hashes: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^([A-Za-z0-9._-]+)==([^\s\\]+)", line)
        if match:
            if name:
                pinned[name] = (version, frozenset(hashes))
            name = match.group(1).lower().replace("_", "-")
            version = match.group(2)
            hashes = set()
        elif line.strip().startswith("--hash="):
            hashes.add(line.strip().removeprefix("--hash=").rstrip(" \\"))
    if name:
        pinned[name] = (version, frozenset(hashes))
    return pinned


def test_document_skill_acquisition_dependencies_track_the_sandbox_lock() -> None:
    acquisition = _parse_pinned_requirements(ROOT / "scripts/document-skills-requirements.txt")
    sandbox = _parse_pinned_requirements(ROOT / "services/sandbox/requirements.lock")

    assert "pydantic" in acquisition
    for name, pin in acquisition.items():
        assert name in sandbox, f"{name} is not pinned by the sandbox lock"
        assert pin == sandbox[name], f"{name} drifted from the sandbox lock"
        assert pin[1], f"{name} must carry hashes for --require-hashes"


def test_document_images_install_the_acquisition_dependencies_before_acquiring() -> None:
    for relative in ("services/worker/Dockerfile", "services/sandbox/Dockerfile"):
        dockerfile = (ROOT / relative).read_text(encoding="utf-8")
        install_index = dockerfile.find("scripts/document-skills-requirements.txt")
        acquire_index = dockerfile.find("scripts/acquire-skills.py --lock")
        assert install_index != -1, f"{relative} must install the pinned acquisition dependencies"
        assert "--require-hashes" in dockerfile, f"{relative} must verify acquisition dependency hashes"
        assert install_index < acquire_index, f"{relative} must install before acquiring"


def test_web_skill_is_acquired_independently_of_document_terms() -> None:
    for relative in ("services/worker/Dockerfile", "services/sandbox/Dockerfile"):
        dockerfile = (ROOT / relative).read_text(encoding="utf-8")
        web_acquire = "--pack web --destination /opt/web-skills"
        document_acquire = "--pack documents --destination /opt/document-skills"
        terms_gate = 'if [ -n "$EDA_DOCUMENT_TERMS_ACCEPTED" ]'

        assert web_acquire in dockerfile
        assert document_acquire in dockerfile
        assert dockerfile.index(web_acquire) < dockerfile.index(terms_gate)
        assert dockerfile.index(terms_gate) < dockerfile.index(document_acquire)
        assert "COPY --from=document-skills /opt/web-skills /opt/web-skills" in dockerfile


def test_document_doctor_is_terms_bound_and_content_free() -> None:
    source = (ROOT / "scripts/doctor-documents.sh").read_text(encoding="utf-8")
    assert "set -euo pipefail" in source
    assert "SKIP: Document Pack intentionally disabled" in source
    assert "EDA_DOCUMENT_TERMS_ACCEPTED" in source
    assert "sha256sum skills.lock.json" in source
    assert 'docker pull --platform linux/amd64 "$image"' in source
    assert "docker create --platform linux/amd64" in source
    assert 'docker cp "${container_id}:/opt/document-skills"' in source
    assert 'docker cp "${container_id}:/opt/web-skills/web-artifacts-builder"' in source
    assert source.count("--no-same-owner --no-same-permissions") == 2
    assert 'chmod -R u+rwX "$extract_root"' in source
    assert 'docker rm --force "$container_id"' in source
    assert "docker run" not in source
    assert "@sha256:" in source
    assert "/opt/web-skills/web-artifacts-builder" in source
    assert "/opt/document-skills" in source
    assert 'if entry["name"] in names' in source
    for name in ("docx", "pdf", "pptx", "xlsx"):
        assert name in source
    assert source.index('if [[ "$phase" == "prebuild" ]]') < source.index("SKIP: Document Pack intentionally disabled")
    assert "PASS: mandatory Web Artifact Pack verified; Document Pack intentionally disabled" in source
    assert "temporary.chmod(0o600)" in source
    assert '"workerImageDigest": worker_digest' in source
    assert "web_identity = {" in source
    assert '"commit": web_record["commit"]' in source
    assert '"skillMdSha256": web_record["skillMdSha256"]' in source
    assert '"licenseSha256": web_record["licenseSha256"]' in source
    assert 'print(json.dumps({"bundles": bundles, "commit": expected_commit, "web": web_identity}' in source
    assert '"verifiedAt"' not in source
    assert "print(record" not in source
    assert "cat /opt/document-skills" not in source


def test_worker_build_passes_only_the_reviewed_terms_hash_as_document_build_argument() -> None:
    build_script = (ROOT / "scripts/build-worker-image.sh").read_text(encoding="utf-8")
    dockerfile = (ROOT / "services/worker/Dockerfile").read_text(encoding="utf-8")
    assert '--build-arg "EDA_DOCUMENT_TERMS_ACCEPTED=${document_terms_accepted}"' in build_script
    assert 'ARG EDA_DOCUMENT_TERMS_ACCEPTED=""' in dockerfile
    assert "scripts/acquire-skills.py" in dockerfile
    assert "scripts/validate-skills.py" in dockerfile
    assert "/opt/document-skills" in dockerfile


def test_sandbox_acquires_skills_inside_the_gated_image_build() -> None:
    dockerfile = (ROOT / "services/sandbox/Dockerfile").read_text(encoding="utf-8")
    build_script = (ROOT / "scripts/build-sandbox-image.sh").read_text(encoding="utf-8")
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")

    assert 'ARG EDA_DOCUMENT_TERMS_ACCEPTED=""' in dockerfile
    assert "scripts/acquire-skills.py" in dockerfile
    assert "scripts/validate-skills.py" in dockerfile
    assert "COPY services/worker/document-skills /opt/document-skills" not in dockerfile
    assert '--build-arg "EDA_DOCUMENT_TERMS_ACCEPTED=${document_terms_accepted}"' in build_script
    assert 'uv run python "$project_root/scripts/acquire-skills.py"' not in build_script
    assert "services/worker/document-skills/*" in dockerignore
    assert "**/.terraform" in dockerignore
    assert "!services/worker/document-skills/build-bundles.py" in dockerignore
    assert "!.artifacts/model-contract.json" in dockerignore
    assert "!.artifacts/tokenizer-calibration.json" in dockerignore
    assert 'azd env set EDA_SANDBOX_IMAGE "$image"' in build_script
    assert 'azd env set EDA_SANDBOX_IMAGE_DIGEST "$digest"' in build_script

    sandbox_deploy = (ROOT / "scripts/deploy-sandbox-group.sh").read_text(encoding="utf-8")
    assert sandbox_deploy.count("EDA_SANDBOX_IMAGE_DIGEST=$sandbox_digest") == 2


def test_deploy_smoke_binds_document_intent_to_health_and_running_worker_digest() -> None:
    source = (ROOT / "scripts/deploy-smoke.sh").read_text(encoding="utf-8")

    assert "document-pack-intent" in source
    assert ".featurePacks.documents" in source
    assert "configured | ready" in source
    assert ".worker.image" in source
    assert ".worker.digest" in source
    assert "EDA_WORKER_IMAGE_DIGEST" in source
    assert "EDA_SANDBOX_IMAGE_DIGEST" in source
    assert "sandbox-image.json" in source
    assert "doctor-documents.sh --phase postdeploy" in source
