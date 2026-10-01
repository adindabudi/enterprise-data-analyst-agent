from __future__ import annotations

from uuid import UUID

import pytest
from eda_api.config import Settings
from eda_fabric_auth import FabricProvider
from pydantic import ValidationError


def settings_values(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "public_origin": "http://localhost:8000",
        "entra_tenant_id": "11111111-1111-1111-1111-111111111111",
        "entra_client_id": "22222222-2222-2222-2222-222222222222",
        "cosmos_endpoint": "https://enterprise-data-analyst.documents.azure.com",
        "blob_account_url": "https://enterprisedataanalyst.blob.core.windows.net",
        "redis_url": "redis://127.0.0.1:6379/0",
        "foundry_project_endpoint": "https://example.services.ai.azure.com/api/projects/example",
        "foundry_model_deployment": "analysis-opus",
        "managed_identity_client_id": "33333333-3333-3333-3333-333333333333",
        "fabric_enabled": False,
        "fabric_provider": None,
    }
    values.update(overrides)
    return values


def test_settings_values_isolate_ambient_fabric_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FABRIC_ENABLED", "true")
    monkeypatch.setenv("FABRIC_PROVIDER", "ontology")

    settings = Settings.model_validate(settings_values())

    assert settings.fabric_enabled is False
    assert settings.fabric_provider is None


def test_settings_exposes_authority_and_redirect_uri() -> None:
    settings = Settings.model_validate(settings_values())

    assert settings.authority == "https://login.microsoftonline.com/11111111-1111-1111-1111-111111111111"
    assert settings.redirect_uri == "http://localhost:8000/api/auth/callback"
    assert settings.entra_tenant_id == UUID("11111111-1111-1111-1111-111111111111")
    assert settings.cosmos_workspace_container == "workspace"
    assert settings.cosmos_auth_container == "auth"
    assert settings.blob_quarantine_container == "quarantine"
    assert settings.blob_sessions_container == "sessions"


def test_development_selects_terra_without_an_independent_base_model_override() -> None:
    settings = Settings.model_validate(settings_values())

    assert settings.eda_model_profile == "gpt-5.6-terra-medium-v1"
    assert settings.foundry_hosting == "azure"
    assert not hasattr(settings, "foundry_expected_base_model")


def test_terra_profile_rejects_anthropic_hosting() -> None:
    with pytest.raises(ValidationError, match=r"Terra.*Azure"):
        Settings.model_validate(settings_values(foundry_hosting="anthropic"))


def test_production_requires_managed_identity_client_id() -> None:
    with pytest.raises(ValidationError, match="managed_identity_client_id is required when app_env is production"):
        Settings.model_validate(settings_values(app_env="production", managed_identity_client_id=None))


def test_production_rejects_client_secret() -> None:
    local_client_secret = "-".join(("local", "secret"))

    with pytest.raises(ValidationError, match="client secret"):
        Settings.model_validate(settings_values(app_env="production", entra_client_secret=local_client_secret))


@pytest.mark.parametrize(
    "overrides",
    [
        {
            "app_env": "production",
            "redis_url": "redis://cache.example:6379/0",
        },
        {
            "app_env": "production",
            "redis_url": "https://cache.example:10000/0",
        },
    ],
)
def test_production_requires_tls_runtime_endpoints(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="must use TLS"):
        Settings.model_validate(settings_values(**overrides))


def test_development_requires_identity_credential() -> None:
    with pytest.raises(ValidationError, match="managed_identity_client_id or entra_client_secret is required"):
        Settings.model_validate(settings_values(managed_identity_client_id=None))


def test_powerbi_pack_is_rejected_in_this_release() -> None:
    with pytest.raises(ValidationError, match="Power BI Project Pack is unavailable"):
        Settings.model_validate(settings_values(powerbi_project_enabled=True))


def test_local_client_secret_is_redacted_from_repr() -> None:
    local_client_secret = "-".join(("local", "secret"))
    settings = Settings.model_validate(
        settings_values(managed_identity_client_id=None, entra_client_secret=local_client_secret)
    )

    assert local_client_secret not in repr(settings)
    assert "entra_client_secret" not in repr(settings)


def test_api_allows_enabled_ontology_auth_with_optional_direct_query_catalog() -> None:
    settings = Settings.model_validate(
        settings_values(
            fabric_enabled=True,
            fabric_provider="ontology",
            fabric_tenant_id="33333333-3333-3333-3333-333333333333",
            fabric_client_id="44444444-4444-4444-4444-444444444444",
            fabric_key_vault_url="https://fabric.vault.azure.net",
            fabric_signing_certificate_name="fabric-oauth-signing",
            fabric_cache_wrap_key_name="fabric-cache-wrap",
            fabric_ontologies={
                "lamna-healthcare": {
                    "workspaceId": "55555555-5555-5555-5555-555555555555",
                    "ontologyId": "66666666-6666-6666-6666-666666666666",
                    "description": "Lamna healthcare operations ontology",
                    "routingTerms": ["patients", "pasien"],
                }
            },
        )
    )

    assert settings.fabric_provider is FabricProvider.ONTOLOGY
    assert tuple(settings.fabric_ontologies) == ("lamna-healthcare",)


@pytest.mark.parametrize("catalog", ["powerbi_project_models", "POWERBI_PROJECT_MODELS_JSON"])
def test_api_rejects_target_catalog_settings(catalog: str) -> None:
    with pytest.raises(ValidationError, match="must not receive provider target catalogs"):
        Settings.model_validate(settings_values(**{catalog: {"untrusted": "target"}}))


def test_api_loads_provider_and_auth_values_from_fabric_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    environment = {
        "EDA_PUBLIC_ORIGIN": "http://localhost:8000",
        "EDA_ENTRA_TENANT_ID": "11111111-1111-1111-1111-111111111111",
        "EDA_ENTRA_CLIENT_ID": "22222222-2222-2222-2222-222222222222",
        "EDA_ENTRA_CLIENT_SECRET": "local-only",
        "EDA_COSMOS_ENDPOINT": "https://enterprise-data-analyst.documents.azure.com",
        "EDA_BLOB_ACCOUNT_URL": "https://enterprisedataanalyst.blob.core.windows.net",
        "EDA_REDIS_URL": "redis://127.0.0.1:6379/0",
        "EDA_FOUNDRY_PROJECT_ENDPOINT": "https://example.services.ai.azure.com/api/projects/example",
        "EDA_FOUNDRY_MODEL_DEPLOYMENT": "analysis-opus",
        "FABRIC_ENABLED": "true",
        "FABRIC_PROVIDER": "ontology",
        "FABRIC_TENANT_ID": "33333333-3333-3333-3333-333333333333",
        "FABRIC_CLIENT_ID": "44444444-4444-4444-4444-444444444444",
        "FABRIC_KEY_VAULT_URL": "https://fabric.vault.azure.net",
        "FABRIC_SIGNING_CERTIFICATE_NAME": "fabric-oauth-signing",
        "FABRIC_CACHE_WRAP_KEY_NAME": "fabric-cache-wrap",
        "FABRIC_ONTOLOGIES_JSON": (
            '{"lamna-healthcare":{"workspaceId":"55555555-5555-5555-5555-555555555555",'
            '"ontologyId":"66666666-6666-6666-6666-666666666666",'
            '"description":"Lamna healthcare operations ontology","routingTerms":["patients","pasien"]}}'
        ),
    }
    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    settings = Settings()

    assert settings.fabric_provider == "ontology"
    assert tuple(settings.fabric_ontologies) == ("lamna-healthcare",)


def test_api_accepts_document_pack_enablement_without_document_paths() -> None:
    settings = Settings.model_validate(settings_values(documents_enabled=True))

    assert settings.documents_enabled is True
