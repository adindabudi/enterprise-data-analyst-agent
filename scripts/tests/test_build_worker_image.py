from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "build-worker-image.sh"


def test_worker_image_is_remote_built_scanned_and_persisted_by_digest() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "az acr build" in source
    assert '--platform "linux/amd64"' in source
    assert '--source-acr-auth-id "[caller]"' in source
    assert "AbacRepositoryPermissions" in source
    assert "LegacyRegistryPermissions" in source
    assert "roleAssignmentMode" in source
    assert 'repository="enterprise-data-analyst/eda-worker"' in source
    assert "services/worker/Dockerfile" in source
    assert '--build-arg "SOURCE_REVISION=${source_revision}"' in source
    assert 'documents_enabled="${DOCUMENTS_ENABLED:-$(azd_optional_value DOCUMENTS_ENABLED)}"' in source
    assert 'documents_enabled="${documents_enabled:-false}"' in source
    assert (
        'document_terms_accepted='
        '"${EDA_DOCUMENT_TERMS_ACCEPTED:-$(azd_optional_value EDA_DOCUMENT_TERMS_ACCEPTED)}"'
    ) in source
    assert '--build-arg "EDA_DOCUMENT_TERMS_ACCEPTED=${document_terms_accepted}"' in source
    assert "trivy image --image-src remote --platform linux/amd64" in source
    assert "--ignore-unfixed" in source
    assert "syft" in source
    assert 'azd env set EDA_WORKER_IMAGE "$image"' in source
    assert 'azd env set EDA_WORKER_IMAGE_DIGEST "$digest"' in source
    assert 'azd env set AZD_AGENT_SKIP_ACR "true"' in source
    assert "docker build" not in source
