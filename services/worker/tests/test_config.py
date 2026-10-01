from __future__ import annotations

import pytest
from eda_worker.config import WorkerSettings
from pydantic import ValidationError


def settings_values(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "app_env": "production",
        "managed_identity_client_id": "11111111-1111-1111-1111-111111111111",
        "entra_client_id": "33333333-3333-3333-3333-333333333333",
        "cosmos_endpoint": "https://example.documents.azure.com",
        "blob_account_url": "https://example.blob.core.windows.net",
        "redis_url": "rediss://cache.southeastasia.redis.azure.net:10000/0",
        "foundry_project_endpoint": "https://example.services.ai.azure.com/api/projects/eda-project",
        "foundry_model_deployment": "gpt-5.6-terra",
        "eda_model_profile": "gpt-5.6-terra-medium-v1",
        "sandbox_subscription_id": "22222222-2222-2222-2222-222222222222",
        "sandbox_resource_group": "rg-eda-production",
        "sandbox_group": "sbg-eda-production",
        "sandbox_region": "southeastasia",
        "sandbox_disk_image_id": "disk_12345678",
    }
    values.update(overrides)
    return values


def test_production_worker_settings_require_all_secure_service_transports() -> None:
    settings = WorkerSettings.model_validate(settings_values())

    assert settings.eda_model_profile == "gpt-5.6-terra-medium-v1"

    with pytest.raises(ValidationError, match="redis_url"):
        WorkerSettings.model_validate(settings_values(redis_url="redis://localhost:6379/0"))


def test_worker_reads_the_foundry_project_endpoint_by_its_azd_name(monkeypatch: pytest.MonkeyPatch) -> None:
    environment = {
        "EDA_ENTRA_CLIENT_ID": "33333333-3333-3333-3333-333333333333",
        "EDA_COSMOS_ENDPOINT": "https://example.documents.azure.com",
        "EDA_BLOB_ACCOUNT_URL": "https://example.blob.core.windows.net",
        "EDA_REDIS_URL": "rediss://cache.southeastasia.redis.azure.net:10000/0",
        "FOUNDRY_PROJECT_ENDPOINT": "https://example.services.ai.azure.com/api/projects/eda-project",
        "EDA_FOUNDRY_MODEL_DEPLOYMENT": "gpt-5.6-terra",
        "EDA_SANDBOX_SUBSCRIPTION_ID": "22222222-2222-2222-2222-222222222222",
        "EDA_SANDBOX_RESOURCE_GROUP": "rg-eda-production",
        "EDA_SANDBOX_GROUP": "sbg-eda-production",
        "EDA_SANDBOX_REGION": "southeastasia",
        "EDA_SANDBOX_DISK_IMAGE_ID": "disk_12345678",
    }
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("EDA_FOUNDRY_PROJECT_ENDPOINT", raising=False)

    settings = WorkerSettings(_env_file=None)

    assert str(settings.foundry_project_endpoint).rstrip("/") == environment["FOUNDRY_PROJECT_ENDPOINT"]
