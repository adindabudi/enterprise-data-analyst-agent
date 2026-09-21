from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "build-sandbox-image.sh"


def test_disabled_documents_force_empty_terms_build_argument() -> None:
    script = SCRIPT.read_text(encoding="utf-8")

    assert 'documents_enabled="${DOCUMENTS_ENABLED:-$(azd_optional_value DOCUMENTS_ENABLED)}"' in script
    assert 'documents_enabled="${documents_enabled:-false}"' in script
    assert 'document_terms_accepted=""' in script
    assert (
        "true)\n        document_terms_accepted="
        '"${EDA_DOCUMENT_TERMS_ACCEPTED:-$(azd_optional_value EDA_DOCUMENT_TERMS_ACCEPTED)}"'
    ) in script
    assert '--build-arg "EDA_DOCUMENT_TERMS_ACCEPTED=${document_terms_accepted}"' in script
    assert '--build-arg "EDA_DOCUMENT_TERMS_ACCEPTED=${EDA_DOCUMENT_TERMS_ACCEPTED:-}"' not in script


def test_vulnerability_scan_has_explicit_bounded_timeout() -> None:
    script = SCRIPT.read_text(encoding="utf-8")

    assert '--platform "linux/amd64"' in script
    assert '--source-acr-auth-id "[caller]"' in script
    assert "trivy image --image-src remote --platform linux/amd64 --timeout 60m" in script
    assert "--exit-code 1 --severity HIGH,CRITICAL --ignore-unfixed" in script
    assert "AbacRepositoryPermissions" in script
    assert "LegacyRegistryPermissions" in script
    assert "roleAssignmentMode" in script
