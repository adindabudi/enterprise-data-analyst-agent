from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_fabric_auth_keeps_only_declared_crypto_data_actions() -> None:
    source = read("infra/terraform/modules/fabric_auth/main.tf")

    for action in (
        "Microsoft.KeyVault/vaults/certificates/read",
        "Microsoft.KeyVault/vaults/certificates/create/action",
        "Microsoft.KeyVault/vaults/keys/sign/action",
        "Microsoft.KeyVault/vaults/keys/wrap/action",
        "Microsoft.KeyVault/vaults/keys/unwrap/action",
    ):
        assert action in source
    for forbidden in (
        "Microsoft.KeyVault/vaults/secrets/read",
        "Microsoft.KeyVault/vaults/secrets/write",
        "Microsoft.KeyVault/vaults/keys/decrypt/action",
        "Microsoft.KeyVault/vaults/keys/export/action",
    ):
        assert forbidden not in source
    assert "enableRbacAuthorization" in source and "true" in source
    assert "enablePurgeProtection" in source and "true" in source
    assert "softDeleteRetentionInDays = 90" in source
    assert 'cleanupPreference = "OnSuccess"' in source
    assert "az keyvault certificate create" in source


def test_root_and_api_contract_support_only_the_ontology_provider() -> None:
    variables = read("infra/terraform/variables.tf")
    container_apps = read("infra/terraform/modules/container_apps/main.tf")

    assert 'variable "fabric_provider"' in variables
    assert 'contains(["", "ontology"], var.fabric_provider)' in variables
    assert 'var.fabric_provider == "ontology" ? [' in container_apps
    assert "FABRIC_ONTOLOGIES_JSON" in container_apps
    assert "FABRIC_CLIENT_SECRET" not in container_apps
    assert "FABRIC_ACCESS_TOKEN" not in container_apps
    assert "FABRIC_MCP_URL" not in container_apps
