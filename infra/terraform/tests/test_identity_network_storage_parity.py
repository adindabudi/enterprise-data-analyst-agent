import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_identities_match_bicep_runtime_shape() -> None:
    source = read("infra/terraform/modules/identities/main.tf")

    assert "id-eda-web-${var.suffix}" in source
    assert "id-eda-worker-${var.suffix}" in source
    assert "id-eda-session-init-${var.suffix}" in source
    assert 'resource "azurerm_user_assigned_identity" "web"' in source
    assert 'resource "azurerm_user_assigned_identity" "worker"' in source
    assert 'resource "azurerm_user_assigned_identity" "session_init"' in source


def test_network_keeps_exact_subnet_and_private_dns_layout() -> None:
    source = read("infra/terraform/modules/network/main.tf")

    assert 'address_space       = ["10.42.0.0/16"]' in source
    assert 'address_prefixes     = ["10.42.0.0/23"]' in source
    assert "10.42.2.0/24" in source
    assert 'name                 = "foundry-agents-v2"' in source
    assert 'address_prefixes     = ["10.42.7.0/24"]' in source
    assert 'name                = "privatelink.blob.core.windows.net"' in source
    assert 'name                = "privatelink.documents.azure.com"' in source
    assert 'name                = "privatelink.redis.azure.net"' in source
    assert 'var.profile == "production" ? 1 : 0' in source


def test_foundry_replacement_has_a_fresh_account_name() -> None:
    source = read("infra/terraform/main.tf")

    assert 'foundry_account    = substr("foundry-eda-${var.environment_name}-${local.name_suffix}-v2"' in source


def test_monitoring_and_registry_match_foundation_policy() -> None:
    monitoring = read("infra/terraform/modules/monitoring/main.tf")
    acr = read("infra/terraform/modules/acr/main.tf")

    assert 'sku                 = "PerGB2018"' in monitoring
    assert 'application_type    = "web"' in monitoring
    assert "workspace_id        = azurerm_log_analytics_workspace.this.id" in monitoring
    assert "admin_enabled       = false" in acr
    assert 'sku                 = "Standard"' in acr
    assert "7f951dda-4ed3-4680-a7ca-43fe172d538d" in acr


def test_foundry_project_identity_can_pull_the_hosted_agent_image() -> None:
    foundry = read("infra/terraform/modules/foundry/main.tf")
    acr = read("infra/terraform/modules/acr/main.tf")
    main = read("infra/terraform/main.tf")

    assert 'output "project_principal_id"' in foundry
    assert 'variable "foundry_project_principal_id"' in acr
    assert 'resource "azurerm_role_assignment" "foundry_project_pull"' in acr
    assert "principal_id       = var.foundry_project_principal_id" in acr
    assert re.search(r"foundry_project_principal_id\s*=\s*module\.foundry\.project_principal_id", main)


def test_foundry_project_identity_can_use_the_account_runtime() -> None:
    foundry = read("infra/terraform/modules/foundry/main.tf")

    assert "foundry_user_role" in foundry
    assert 'resource "azurerm_role_assignment" "project_foundry_user"' in foundry
    assert "principal_id       = azapi_resource.project.output.identity.principalId" in foundry
    assert "scope              = azapi_resource.account.id" in foundry
    assert "53ca6127-db72-4b80-b1b0-d745d6d5456d" in foundry


def test_storage_keeps_defender_private_endpoint_and_explicit_blob_roles() -> None:
    source = read("infra/terraform/modules/storage/main.tf")

    assert 'type      = "Microsoft.Storage/storageAccounts@2025-06-01"' in source
    assert "allowSharedKeyAccess" in source
    assert "defaultToOAuthAuthentication = true" in source
    assert 'name      = "quarantine"' in source
    assert 'name      = "sessions"' in source
    assert 'type                      = "Microsoft.Security/defenderForStorageSettings@2025-07-01-preview"' in source
    assert "BlobSoftDelete" in source
    assert "capGBPerMonth = var.defender_scan_cap_gb" in source
    assert "acceptance_blob_data_contributor" in source
    assert "ba92f5b4-2d11-453d-a403-e96b0029c9fe" in source
    assert "pe-${var.account_name}-blob" in source


def test_bicep_quarantine_tag_reader_is_read_only_and_api_scoped() -> None:
    source = read("infra/bicep/modules/storage.bicep")
    assert (
        "var quarantineBlobTagsReaderRoleDefinitionId = subscriptionResourceId( "
        "'Microsoft.Authorization/roleDefinitions', quarantineBlobTagsReaderRole.name )"
    ) in " ".join(source.split())
    role_match = re.search(
        r"resource quarantineBlobTagsReaderRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' = \{(.*?)^\}",
        source,
        re.DOTALL | re.MULTILINE,
    )
    assert role_match is not None
    role = " ".join(role_match.group(1).split())
    assert "name: guid(resourceGroup().id, 'eda-quarantine-blob-tags-reader')" in role
    assert "roleName: 'EDA Quarantine Blob Tags Reader (${accountName})'" in role
    assert "type: 'CustomRole'" in role
    assert "assignableScopes: [ resourceGroup().id ]" in role
    assert (
        "permissions: [ { actions: [] notActions: [] dataActions: [ "
        "'Microsoft.Storage/storageAccounts/blobServices/containers/blobs/tags/read' "
        "] notDataActions: [] } ]"
    ) in role
    assignment_match = re.search(
        r"resource webQuarantineBlobTagsReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = \{(.*?)^\}",
        source,
        re.DOTALL | re.MULTILINE,
    )
    assert assignment_match is not None
    assignment = " ".join(assignment_match.group(1).split())
    assert "name: guid(quarantine.id, webIdentityPrincipalId, quarantineBlobTagsReaderRoleDefinitionId)" in assignment
    assert "scope: quarantine" in assignment
    assert "principalId: webIdentityPrincipalId" in assignment
    assert "principalType: 'ServicePrincipal'" in assignment
    assert source.count("roleDefinitionId: quarantineBlobTagsReaderRoleDefinitionId") == 1


def test_terraform_quarantine_tag_reader_matches_bicep_least_privilege() -> None:
    source = read("infra/terraform/modules/storage/main.tf")
    assert (
        'quarantine_blob_tags_reader_role_id = "/subscriptions/${var.subscription_id}'
        '/providers/Microsoft.Authorization/roleDefinitions/${azapi_resource.quarantine_blob_tags_reader_role.name}"'
    ) in " ".join(source.split())
    role_match = re.search(
        r'resource "azapi_resource" "quarantine_blob_tags_reader_role" \{(.*?)^\}',
        source,
        re.DOTALL | re.MULTILINE,
    )
    assert role_match is not None
    role = " ".join(role_match.group(1).split())
    assert 'type = "Microsoft.Authorization/roleDefinitions@2022-04-01"' in role
    assert (
        'name = uuidv5("11fb06fb-712d-4ddd-98c7-e71bbd588830", '
        '"${local.resource_group_id}-eda-quarantine-blob-tags-reader")'
    ) in role
    assert "parent_id = local.resource_group_id" in role
    assert 'roleName = "EDA Quarantine Blob Tags Reader (${var.account_name})"' in role
    assert 'type = "CustomRole"' in role
    assert "assignableScopes = [local.resource_group_id]" in role
    assert (
        "permissions = [ { actions = [] notActions = [] dataActions = [ "
        '"Microsoft.Storage/storageAccounts/blobServices/containers/blobs/tags/read" '
        "] notDataActions = [] } ]"
    ) in role
    assignment_match = re.search(
        r'resource "azurerm_role_assignment" "web_quarantine_blob_tags_reader" \{(.*?)^\}',
        source,
        re.DOTALL | re.MULTILINE,
    )
    assert assignment_match is not None
    assignment = " ".join(assignment_match.group(1).split())
    assert (
        'name = uuidv5("11fb06fb-712d-4ddd-98c7-e71bbd588830", '
        '"${azapi_resource.quarantine.id}-${var.web_identity_principal_id}-'
        '${local.quarantine_blob_tags_reader_role_id}")'
    ) in assignment
    assert "scope = azapi_resource.quarantine.id" in assignment
    assert "principal_id = var.web_identity_principal_id" in assignment
    assert 'principal_type = "ServicePrincipal"' in assignment
    assert "role_definition_id = local.quarantine_blob_tags_reader_role_id" in assignment
    assignments = re.findall(
        r"role_definition_id\s*=\s*local\.quarantine_blob_tags_reader_role_id",
        source,
    )
    assert len(assignments) == 1
