import pytest


@pytest.fixture
def settings_values() -> dict[str, str]:
    return {
        "public_origin": "http://localhost:8000",
        "entra_tenant_id": "11111111-1111-1111-1111-111111111111",
        "entra_client_id": "22222222-2222-2222-2222-222222222222",
        "entra_client_secret": "local-only",
        "cosmos_endpoint": "https://enterprise-data-analyst.documents.azure.com",
        "blob_account_url": "https://enterprisedataanalyst.blob.core.windows.net",
        "foundry_project_endpoint": "https://example.services.ai.azure.com/api/projects/example",
        "foundry_model_deployment": "analysis-opus",
    }
