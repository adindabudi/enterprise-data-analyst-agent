variable "account_name" {
  type = string
}

variable "location" {
  type = string
}

variable "profile" {
  type = string
}

variable "resource_group_name" {
  type = string
}

variable "subscription_id" {
  type = string
}

variable "tags" {
  type = map(string)
}

variable "defender_scan_cap_gb" {
  type = number
}

variable "web_identity_principal_id" {
  type = string
}

variable "worker_identity_principal_id" {
  type = string
}

variable "acceptance_principal_id" {
  type = string
}

variable "private_endpoints_subnet_id" {
  type = string
}

variable "private_dns_zone_id" {
  type = string
}

locals {
  resource_group_id                   = "/subscriptions/${var.subscription_id}/resourceGroups/${var.resource_group_name}"
  blob_data_contributor_role_id       = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/ba92f5b4-2d11-453d-a403-e96b0029c9fe"
  quarantine_blob_tags_reader_role_id = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/${azapi_resource.quarantine_blob_tags_reader_role.name}"
}

resource "azapi_resource" "storage" {
  type      = "Microsoft.Storage/storageAccounts@2025-06-01"
  name      = var.account_name
  location  = var.location
  parent_id = local.resource_group_id
  tags      = var.tags

  body = {
    kind = "StorageV2"
    sku = {
      name = "Standard_LRS"
    }
    properties = {
      allowBlobPublicAccess        = false
      allowSharedKeyAccess         = false
      defaultToOAuthAuthentication = true
      minimumTlsVersion            = "TLS1_2"
      publicNetworkAccess          = var.profile == "production" ? "Disabled" : "Enabled"
    }
  }
}

resource "azapi_resource" "blob_service" {
  type      = "Microsoft.Storage/storageAccounts/blobServices@2025-06-01"
  name      = "default"
  parent_id = azapi_resource.storage.id

  body = {
    properties = {
      changeFeed = {
        enabled = false
      }
      isVersioningEnabled = true
      deleteRetentionPolicy = {
        enabled = true
        days    = 30
      }
      containerDeleteRetentionPolicy = {
        enabled = true
        days    = 30
      }
    }
  }
}

resource "azapi_resource" "quarantine" {
  type      = "Microsoft.Storage/storageAccounts/blobServices/containers@2025-06-01"
  name      = "quarantine"
  parent_id = azapi_resource.blob_service.id
  body = {
    properties = {
      publicAccess = "None"
    }
  }
}

resource "azapi_resource" "sessions" {
  type      = "Microsoft.Storage/storageAccounts/blobServices/containers@2025-06-01"
  name      = "sessions"
  parent_id = azapi_resource.blob_service.id
  body = {
    properties = {
      publicAccess = "None"
    }
  }
}

resource "azapi_resource" "defender" {
  type                      = "Microsoft.Security/defenderForStorageSettings@2025-07-01-preview"
  name                      = "current"
  parent_id                 = azapi_resource.storage.id
  schema_validation_enabled = false

  body = {
    properties = {
      isEnabled                         = true
      overrideSubscriptionLevelSettings = true
      malwareScanning = {
        blobScanResultsOptions = "BlobIndexTags"
        automatedResponse      = "BlobSoftDelete"
        onUpload = {
          isEnabled     = true
          capGBPerMonth = var.defender_scan_cap_gb
        }
      }
      sensitiveDataDiscovery = {
        isEnabled = false
      }
    }
  }
}

resource "azapi_resource" "quarantine_blob_tags_reader_role" {
  type      = "Microsoft.Authorization/roleDefinitions@2022-04-01"
  name      = uuidv5("11fb06fb-712d-4ddd-98c7-e71bbd588830", "${local.resource_group_id}-eda-quarantine-blob-tags-reader")
  parent_id = local.resource_group_id

  body = {
    properties = {
      roleName    = "EDA Quarantine Blob Tags Reader (${var.account_name})"
      description = "Read Defender scan result tags on quarantined uploads without changing tags or blob content."
      type        = "CustomRole"
      permissions = [
        {
          actions    = []
          notActions = []
          dataActions = [
            "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/tags/read"
          ]
          notDataActions = []
        }
      ]
      assignableScopes = [local.resource_group_id]
    }
  }
}

resource "azurerm_role_assignment" "web_quarantine_blob_tags_reader" {
  name               = uuidv5("11fb06fb-712d-4ddd-98c7-e71bbd588830", "${azapi_resource.quarantine.id}-${var.web_identity_principal_id}-${local.quarantine_blob_tags_reader_role_id}")
  principal_id       = var.web_identity_principal_id
  principal_type     = "ServicePrincipal"
  role_definition_id = local.quarantine_blob_tags_reader_role_id
  scope              = azapi_resource.quarantine.id
}

resource "azurerm_role_assignment" "web_blob_data_contributor" {
  principal_id       = var.web_identity_principal_id
  principal_type     = "ServicePrincipal"
  role_definition_id = local.blob_data_contributor_role_id
  scope              = azapi_resource.storage.id
}

resource "azurerm_role_assignment" "worker_blob_data_contributor" {
  principal_id       = var.worker_identity_principal_id
  principal_type     = "ServicePrincipal"
  role_definition_id = local.blob_data_contributor_role_id
  scope              = azapi_resource.storage.id
}

resource "azurerm_role_assignment" "acceptance_blob_data_contributor" {
  count              = var.acceptance_principal_id != "" ? 1 : 0
  principal_id       = var.acceptance_principal_id
  principal_type     = "ServicePrincipal"
  role_definition_id = local.blob_data_contributor_role_id
  scope              = azapi_resource.storage.id
}

resource "azapi_resource" "private_endpoint" {
  count                     = var.profile == "production" ? 1 : 0
  type                      = "Microsoft.Network/privateEndpoints@2024-10-01"
  name                      = "pe-${var.account_name}-blob"
  location                  = var.location
  parent_id                 = local.resource_group_id
  schema_validation_enabled = false
  tags                      = var.tags

  body = {
    properties = {
      subnet = {
        id = var.private_endpoints_subnet_id
      }
      privateLinkServiceConnections = [
        {
          name = "storage-blob"
          properties = {
            privateLinkServiceId = azapi_resource.storage.id
            groupIds             = ["blob"]
          }
        }
      ]
    }
  }
}

resource "azapi_resource" "private_dns_zone_group" {
  count                     = var.profile == "production" ? 1 : 0
  type                      = "Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-10-01"
  name                      = "default"
  parent_id                 = azapi_resource.private_endpoint[0].id
  schema_validation_enabled = false

  body = {
    properties = {
      privateDnsZoneConfigs = [
        {
          name = "storage-blob"
          properties = {
            privateDnsZoneId = var.private_dns_zone_id
          }
        }
      ]
    }
  }
}

output "account_id" {
  value = azapi_resource.storage.id
}

output "blob_endpoint" {
  value = "https://${var.account_name}.blob.core.windows.net/"
}