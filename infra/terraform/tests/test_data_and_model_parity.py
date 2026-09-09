import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_cosmos_matches_container_and_acceptance_rbac_contract() -> None:
    source = read("infra/terraform/modules/cosmos/main.tf")

    assert 'type      = "Microsoft.DocumentDB/databaseAccounts@2025-04-15"' in source
    assert "disableLocalAuth         = true" in source
    assert "enableAutomaticFailover  = true" in source
    assert 'paths   = ["/tenantId", "/ownerObjectId", "/sessionId"]' in source
    assert 'name      = "auth"' in source
    assert "defaultTtl = -1" in source
    assert 'name      = "runtime"' in source
    assert 'name      = "fabricAuth"' in source
    assert "acceptance_cosmos_data_contributor" in source
    assert "00000000-0000-0000-0000-000000000002" in source


def test_redis_keeps_exact_api_version_and_runtime_shape_without_legacy_modules() -> None:
    redis = read("infra/terraform/modules/redis/main.tf")

    assert "Microsoft.Cache/redisEnterprise@2025-07-01" in redis
    assert 'accessKeysAuthentication = "Disabled"' in redis
    assert 'clientProtocol           = "Encrypted"' in redis
    assert "port                     = 10000" in redis
    assert not (ROOT / "infra/terraform/modules/durable_task/main.tf").exists()
    assert not (ROOT / "infra/terraform/modules/session_pool/main.tf").exists()
    assert not (ROOT / "infra/bicep/modules/durable-task.bicep").exists()
    assert not (ROOT / "infra/bicep/modules/session-pool.bicep").exists()


def test_foundry_is_terra_only_with_no_partner_model_surface() -> None:
    foundry = read("infra/terraform/modules/foundry/main.tf")
    network = read("infra/terraform/modules/network/main.tf")
    main = read("infra/terraform/main.tf")
    variables = read("infra/terraform/variables.tf")

    assert 'format  = "OpenAI"' in foundry
    assert 'name    = "gpt-5.6-terra"' in foundry
    assert 'version = "2026-07-09"' in foundry
    assert 'name     = "GlobalStandard"' in foundry
    assert "claude" not in foundry.lower()
    assert "anthropic" not in foundry.lower()
    assert "modelProviderData" not in foundry
    assert 'variable "model_profile"' in variables
    assert 'variable "model_capacity"' in variables
    assert 'variable "model_version"' not in variables
    assert 'resource "azurerm_subnet" "foundry_agents"' in network
    assert 'address_prefixes     = ["10.42.7.0/24"]' in network
    assert 'name    = "Microsoft.App/environments"' in network
    assert "networkInjections" in foundry
    assert 'scenario                   = "agent"' in foundry
    assert "subnetArmId                = var.agent_subnet_id" in foundry
    assert "useMicrosoftManagedNetwork = false" in foundry
    assert "agent_subnet_id" in main
    assert 'variable "web_identity_principal_id"' in foundry
    assert "eed3b665-ab3a-47b6-8f48-c9382fb1dad6" in foundry
    assert "UserIdentityImpersonation/action" in foundry
    assert 'resource "azurerm_role_assignment" "web_foundry_agent_consumer"' in foundry
    assert 'resource "azurerm_role_assignment" "web_hosted_user_impersonation"' in foundry
    assert "scope              = azapi_resource.project.id" in foundry
    assert re.search(
        r"web_identity_principal_id\s*=\s*module\.identities\.web_identity_principal_id",
        main,
    )
