from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "pin-application-images.sh"


def test_api_revision_is_promoted_with_hosted_agent_gates_and_worker_digest() -> None:
    script = SCRIPT.read_text(encoding="utf-8")
    api_update = script.index('az containerapp update --name "$api_name"')
    image_manifest = script.index('temporary_manifest="$(mktemp')
    promoted_slice = script[api_update:image_manifest]

    assert api_update < image_manifest
    assert 'az containerapp update --name "$worker_name"' not in script
    assert '--image "$api_image"' in promoted_slice
    for binding in (
        'EDA_WORKER_IMAGE_DIGEST="$worker_digest"',
        "EDA_MODEL_CONTRACT_VERIFIED=true",
        "EDA_TOKENIZER_CALIBRATED=true",
        "EDA_HOSTED_AGENT_ENABLED=true",
        "EDA_ENTRA_FEDERATION_READY=true",
        "EDA_CORE_READY=true",
    ):
        assert binding in promoted_slice


def test_script_verifies_promoted_api_readiness_environment() -> None:
    script = SCRIPT.read_text(encoding="utf-8")

    for name in (
        "EDA_WORKER_IMAGE_DIGEST",
        "EDA_MODEL_CONTRACT_VERIFIED",
        "EDA_TOKENIZER_CALIBRATED",
        "EDA_HOSTED_AGENT_ENABLED",
        "EDA_ENTRA_FEDERATION_READY",
        "EDA_CORE_READY",
    ):
        assert f"verify_api_environment '{name}'" in script


def test_api_revision_waits_for_provisioned_and_healthy_states() -> None:
    script = SCRIPT.read_text(encoding="utf-8")
    wait_function = script[script.index("wait_for_revision()") : script.index("api_source_image=")]

    assert "properties.provisioningState" in wait_function
    assert "properties.healthState" in wait_function
    assert '"$provisioning_state" = "Provisioned"' in wait_function
    assert '"$health_state" = "Healthy"' in wait_function
    assert "properties.runningState" not in wait_function
    assert 'while [ "$attempts" -lt 72 ]' in wait_function
    assert "--output json" in wait_function
    assert "jq -r '.provisioning // empty'" in wait_function
    assert "jq -r '.health // empty'" in wait_function
    assert "read -r provisioning_state health_state" not in wait_function


def test_retry_accepts_an_already_digest_pinned_api_image() -> None:
    script = SCRIPT.read_text(encoding="utf-8")
    resolver = script[script.index("resolve_digest()") : script.index("wait_for_revision()")]

    assert '"${registry_login_server}"/*@sha256:' in resolver
    assert 'digest="${source_image##*@}"' in resolver
    assert 'resolved_image="$source_image"' in resolver


def test_worker_image_comes_from_the_prebuilt_immutable_environment_value() -> None:
    script = SCRIPT.read_text(encoding="utf-8")

    assert 'worker_image="$(azd_value EDA_WORKER_IMAGE)"' in script
    assert 'worker_digest="${worker_image##*@}"' in script
    assert (
        'worker_source_image="${registry_login_server}/enterprise-data-analyst/eda-worker:${environment_name}"'
        not in script
    )
