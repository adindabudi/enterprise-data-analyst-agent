from __future__ import annotations

import json
from uuid import UUID, uuid4

import pytest
from eda_worker.fabric.config import FabricSettings, SemanticModelTarget
from eda_worker.fabric.ontology.config import OntologyTarget
from pydantic import ValidationError


def auth_values() -> dict[str, object]:
    return {
        "tenant_id": uuid4(),
        "client_id": uuid4(),
        "key_vault_url": "https://example.vault.azure.net",
        "signing_certificate_name": "fabric-oauth-signing",
        "cache_wrap_key_name": "fabric-cache-wrap",
    }


def semantic_models() -> dict[str, SemanticModelTarget]:
    return {
        "sales": SemanticModelTarget(
            model_id=uuid4(),
            description="Curated sales measures and fiscal dimensions.",
        )
    }


def ontologies() -> dict[str, OntologyTarget]:
    return {
        "lamna-healthcare": OntologyTarget(
            workspace_id=uuid4(),
            ontology_id=uuid4(),
            description="Synthetic hospital operations.",
        )
    }


@pytest.mark.parametrize("provider", ["semantic_model", "ontology"])
def test_enabled_configuration_requires_explicit_provider_catalog(provider: str) -> None:
    with pytest.raises(ValidationError, match="selected provider"):
        FabricSettings(enabled=True, provider=provider, **auth_values())


def test_provider_catalogs_are_mutually_exclusive() -> None:
    with pytest.raises(ValidationError, match="selected provider"):
        FabricSettings(
            enabled=True,
            provider="ontology",
            models=semantic_models(),
            ontologies=ontologies(),
            **auth_values(),
        )


def test_disabled_configuration_rejects_provider_and_targets() -> None:
    with pytest.raises(ValidationError, match="disabled"):
        FabricSettings(enabled=False, provider="ontology", ontologies=ontologies())


def test_disabled_hosted_environment_normalizes_empty_optional_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FABRIC_ENABLED", "false")
    for name in (
        "FABRIC_PROVIDER",
        "FABRIC_TENANT_ID",
        "FABRIC_CLIENT_ID",
        "FABRIC_KEY_VAULT_URL",
        "FABRIC_SIGNING_CERTIFICATE_NAME",
        "FABRIC_CACHE_WRAP_KEY_NAME",
    ):
        monkeypatch.setenv(name, "")
    monkeypatch.setenv("FABRIC_SEMANTIC_MODELS_JSON", "{}")
    monkeypatch.setenv("FABRIC_ONTOLOGIES_JSON", "{}")

    settings = FabricSettings()

    assert settings.enabled is False
    assert settings.provider is None
    assert settings.tenant_id is None
    assert settings.client_id is None
    assert settings.key_vault_url is None


@pytest.mark.parametrize("provider", [None, "unknown"])
def test_enabled_configuration_rejects_missing_or_unknown_provider(provider: str | None) -> None:
    with pytest.raises(ValidationError):
        FabricSettings(enabled=True, provider=provider, models=semantic_models(), **auth_values())


def test_ontology_target_rejects_equal_ids() -> None:
    identifier = uuid4()

    with pytest.raises(ValidationError, match="must differ"):
        OntologyTarget(
            workspace_id=identifier,
            ontology_id=identifier,
            description="Synthetic hospital operations.",
        )


@pytest.mark.parametrize("alias", ["a", "Uppercase", "has_underscore", "x" * 41])
def test_ontology_catalog_rejects_invalid_alias(alias: str) -> None:
    with pytest.raises(ValidationError, match="alias"):
        FabricSettings(
            enabled=True,
            provider="ontology",
            ontologies={alias: next(iter(ontologies().values()))},
            **auth_values(),
        )


def test_ontology_catalog_rejects_duplicate_target_pair() -> None:
    target = next(iter(ontologies().values()))

    with pytest.raises(ValidationError, match="duplicate"):
        FabricSettings(
            enabled=True,
            provider="ontology",
            ontologies={"lamna-one": target, "lamna-two": target},
            **auth_values(),
        )


def test_ontology_catalog_rejects_more_than_twenty_targets() -> None:
    targets = {
        f"ontology-{index:02d}": OntologyTarget(
            workspace_id=UUID(int=index + 1),
            ontology_id=UUID(int=index + 100),
            description="Synthetic hospital operations.",
        )
        for index in range(21)
    }

    with pytest.raises(ValidationError):
        FabricSettings(enabled=True, provider="ontology", ontologies=targets, **auth_values())


def test_target_models_reject_raw_url_fields() -> None:
    with pytest.raises(ValidationError, match="endpoint"):
        OntologyTarget.model_validate(
            {
                "workspaceId": str(uuid4()),
                "ontologyId": str(uuid4()),
                "description": "Synthetic hospital operations.",
                "endpoint": "https://untrusted.example/mcp",
            }
        )


def test_worker_loads_selected_catalog_from_documented_json_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    target = next(iter(ontologies().values()))
    environment = {
        "FABRIC_ENABLED": "true",
        "FABRIC_PROVIDER": "ontology",
        "FABRIC_TENANT_ID": str(uuid4()),
        "FABRIC_CLIENT_ID": str(uuid4()),
        "FABRIC_KEY_VAULT_URL": "https://example.vault.azure.net",
        "FABRIC_SIGNING_CERTIFICATE_NAME": "fabric-oauth-signing",
        "FABRIC_CACHE_WRAP_KEY_NAME": "fabric-cache-wrap",
        "FABRIC_ONTOLOGIES_JSON": json.dumps({"lamna-healthcare": target.model_dump(mode="json", by_alias=True)}),
    }
    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    settings = FabricSettings()

    assert settings.provider == "ontology"
    assert settings.ontologies == {"lamna-healthcare": target}
