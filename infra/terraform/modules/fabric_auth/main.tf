variable "enabled" {
  type = bool
}

variable "location" {
  type = string
}

variable "resource_group_name" {
  type = string
}

variable "subscription_id" {
  type = string
}

variable "tenant_id" {
  type = string
}

variable "tags" {
  type = map(string)
}

variable "vault_name" {
  type = string
}

variable "provisioning_identity_name" {
  type = string
}

variable "signing_certificate_name" {
  type = string
}

variable "cache_wrap_key_name" {
  type = string
}

variable "web_identity_principal_id" {
  type = string
}

variable "acceptance_principal_id" {
  type = string
}

variable "force_update_tag" {
  type = string
}

locals {
  resource_group_id             = "/subscriptions/${var.subscription_id}/resourceGroups/${var.resource_group_name}"
  subscription_scope            = "/subscriptions/${var.subscription_id}"
  certificate_read_role_id      = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/11111111-1111-1111-1111-111111111111"
  certificate_provision_role_id = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/22222222-2222-2222-2222-222222222222"
  signing_role_id               = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/33333333-3333-3333-3333-333333333333"
  cache_crypto_role_id          = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/44444444-4444-4444-4444-444444444444"
}

resource "azapi_resource" "certificate_read_role" {
  count                     = var.enabled ? 1 : 0
  type                      = "Microsoft.Authorization/roleDefinitions@2022-04-01"
  name                      = "11111111-1111-1111-1111-111111111111"
  parent_id                 = local.subscription_scope
  schema_validation_enabled = false

  body = {
    properties = {
      roleName         = "EDA Fabric certificate read"
      description      = "Read public Fabric OAuth certificate metadata only."
      type             = "CustomRole"
      permissions      = [{ actions = [], notActions = [], dataActions = ["Microsoft.KeyVault/vaults/certificates/read"], notDataActions = [] }]
      assignableScopes = [local.resource_group_id]
    }
  }
}

resource "azapi_resource" "certificate_provision_role" {
  count                     = var.enabled ? 1 : 0
  type                      = "Microsoft.Authorization/roleDefinitions@2022-04-01"
  name                      = "22222222-2222-2222-2222-222222222222"
  parent_id                 = local.subscription_scope
  schema_validation_enabled = false

  body = {
    properties = {
      roleName         = "EDA Fabric certificate provision"
      description      = "Create the one self-signed Fabric OAuth certificate."
      type             = "CustomRole"
      permissions      = [{ actions = [], notActions = [], dataActions = ["Microsoft.KeyVault/vaults/certificates/create/action", "Microsoft.KeyVault/vaults/certificates/read"], notDataActions = [] }]
      assignableScopes = [local.resource_group_id]
    }
  }
}

resource "azapi_resource" "signing_role" {
  count                     = var.enabled ? 1 : 0
  type                      = "Microsoft.Authorization/roleDefinitions@2022-04-01"
  name                      = "33333333-3333-3333-3333-333333333333"
  parent_id                 = local.subscription_scope
  schema_validation_enabled = false

  body = {
    properties = {
      roleName         = "EDA Fabric OAuth sign"
      description      = "Sign OAuth client assertions with the certificate backing key only."
      type             = "CustomRole"
      permissions      = [{ actions = [], notActions = [], dataActions = ["Microsoft.KeyVault/vaults/keys/sign/action"], notDataActions = [] }]
      assignableScopes = [local.resource_group_id]
    }
  }
}

resource "azapi_resource" "cache_crypto_role" {
  count                     = var.enabled ? 1 : 0
  type                      = "Microsoft.Authorization/roleDefinitions@2022-04-01"
  name                      = "44444444-4444-4444-4444-444444444444"
  parent_id                 = local.subscription_scope
  schema_validation_enabled = false

  body = {
    properties = {
      roleName         = "EDA Fabric cache wrap and unwrap"
      description      = "Wrap and unwrap Fabric OAuth cache data-encryption keys only."
      type             = "CustomRole"
      permissions      = [{ actions = [], notActions = [], dataActions = ["Microsoft.KeyVault/vaults/keys/wrap/action", "Microsoft.KeyVault/vaults/keys/unwrap/action"], notDataActions = [] }]
      assignableScopes = [local.resource_group_id]
    }
  }
}

resource "azapi_resource" "vault" {
  count                     = var.enabled ? 1 : 0
  type                      = "Microsoft.KeyVault/vaults@2024-11-01"
  name                      = var.vault_name
  location                  = var.location
  parent_id                 = local.resource_group_id
  schema_validation_enabled = false
  tags                      = var.tags

  body = {
    properties = {
      tenantId                  = var.tenant_id
      enableRbacAuthorization   = true
      enablePurgeProtection     = true
      softDeleteRetentionInDays = 90
      publicNetworkAccess       = "Enabled"
      sku = {
        family = "A"
        name   = "standard"
      }
      networkAcls = {
        bypass        = "AzureServices"
        defaultAction = "Allow"
      }
    }
  }
}

resource "azapi_resource" "cache_wrap_key" {
  count                     = var.enabled ? 1 : 0
  type                      = "Microsoft.KeyVault/vaults/keys@2024-11-01"
  name                      = var.cache_wrap_key_name
  parent_id                 = azapi_resource.vault[0].id
  schema_validation_enabled = false

  body = {
    properties = {
      kty     = "RSA"
      keySize = 3072
      keyOps  = ["wrapKey", "unwrapKey"]
      attributes = {
        enabled    = true
        exportable = false
      }
    }
  }
}

resource "azurerm_user_assigned_identity" "provisioning" {
  count               = var.enabled ? 1 : 0
  location            = var.location
  name                = var.provisioning_identity_name
  resource_group_name = var.resource_group_name
  tags                = var.tags
}

resource "azurerm_role_assignment" "provision_certificate_assignment" {
  count              = var.enabled ? 1 : 0
  principal_id       = azurerm_user_assigned_identity.provisioning[0].principal_id
  principal_type     = "ServicePrincipal"
  role_definition_id = local.certificate_provision_role_id
  scope              = azapi_resource.vault[0].id
}

resource "azapi_resource" "certificate_script" {
  count                     = var.enabled ? 1 : 0
  type                      = "Microsoft.Resources/deploymentScripts@2023-08-01"
  name                      = "create-${var.signing_certificate_name}"
  location                  = var.location
  parent_id                 = local.resource_group_id
  schema_validation_enabled = false
  tags                      = var.tags

  body = {
    kind = "AzureCLI"
    identity = {
      type = "UserAssigned"
      userAssignedIdentities = {
        "${azurerm_user_assigned_identity.provisioning[0].id}" = {}
      }
    }
    properties = {
      azCliVersion      = "2.76.0"
      cleanupPreference = "OnSuccess"
      retentionInterval = "P1D"
      timeout           = "PT15M"
      forceUpdateTag    = var.force_update_tag
      environmentVariables = [
        { name = "VAULT_NAME", value = var.vault_name },
        { name = "CERTIFICATE_NAME", value = var.signing_certificate_name },
      ]
      scriptContent = <<-SCRIPT
        set -euo pipefail
        if az keyvault certificate show --vault-name "$VAULT_NAME" --name "$CERTIFICATE_NAME" --only-show-errors >/dev/null 2>&1; then
          exit 0
        fi
        policy="$(az keyvault certificate get-default-policy --output json)"
        for attempt in $(seq 1 18); do
          if az keyvault certificate create --vault-name "$VAULT_NAME" --name "$CERTIFICATE_NAME" --policy "$policy" --only-show-errors >/dev/null; then
            exit 0
          fi
          sleep 10
        done
        exit 1
      SCRIPT
    }
  }
}

resource "azurerm_role_assignment" "web_certificate_read" {
  count              = var.enabled ? 1 : 0
  principal_id       = var.web_identity_principal_id
  principal_type     = "ServicePrincipal"
  role_definition_id = local.certificate_read_role_id
  scope              = azapi_resource.vault[0].id
}

resource "azurerm_role_assignment" "acceptance_certificate_read" {
  count              = var.enabled && var.acceptance_principal_id != "" ? 1 : 0
  principal_id       = var.acceptance_principal_id
  role_definition_id = local.certificate_read_role_id
  scope              = azapi_resource.vault[0].id
}

resource "azurerm_role_assignment" "web_signing" {
  count              = var.enabled ? 1 : 0
  principal_id       = var.web_identity_principal_id
  principal_type     = "ServicePrincipal"
  role_definition_id = local.signing_role_id
  scope              = "${azapi_resource.vault[0].id}/keys/${var.signing_certificate_name}"
}

resource "azurerm_role_assignment" "web_cache_crypto" {
  count              = var.enabled ? 1 : 0
  principal_id       = var.web_identity_principal_id
  principal_type     = "ServicePrincipal"
  role_definition_id = local.cache_crypto_role_id
  scope              = azapi_resource.cache_wrap_key[0].id
}

output "vault_uri" {
  value = var.enabled ? "https://${var.vault_name}.vault.azure.net/" : ""
}