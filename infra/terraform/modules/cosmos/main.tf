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

variable "cosmos_max_throughput" {
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

variable "fabric_enabled" {
  type = bool
}

locals {
  resource_group_id            = "/subscriptions/${var.subscription_id}/resourceGroups/${var.resource_group_name}"
  cosmos_data_contributor_role = "00000000-0000-0000-0000-000000000002"
}

resource "azapi_resource" "account" {
  type      = "Microsoft.DocumentDB/databaseAccounts@2025-04-15"
  name      = var.account_name
  location  = var.location
  parent_id = local.resource_group_id
  tags      = var.tags

  body = {
    kind = "GlobalDocumentDB"
    properties = {
      databaseAccountOfferType = "Standard"
      disableLocalAuth         = true
      enableAutomaticFailover  = true
      publicNetworkAccess      = var.profile == "production" ? "Disabled" : "Enabled"
      consistencyPolicy = {
        defaultConsistencyLevel = "Session"
      }
      locations = [
        {
          locationName     = var.location
          failoverPriority = 0
          isZoneRedundant  = var.profile == "production"
        }
      ]
      backupPolicy = merge(
        { type = var.profile == "production" ? "Continuous" : "Periodic" },
        var.profile == "production" ? {
          continuousModeProperties = { tier = "Continuous30Days" }
        } : {},
        var.profile == "production" ? {} : {
          periodicModeProperties = {
            backupIntervalInMinutes        = 240
            backupRetentionIntervalInHours = 8
            backupStorageRedundancy        = "Geo"
          }
        },
      )
    }
  }
}

resource "azapi_resource" "database" {
  type      = "Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2025-04-15"
  name      = "enterprise-data-analyst"
  parent_id = azapi_resource.account.id

  body = {
    properties = {
      resource = {
        id = "enterprise-data-analyst"
      }
    }
  }
}

resource "azapi_resource" "workspace" {
  type      = "Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2025-04-15"
  name      = "workspace"
  parent_id = azapi_resource.database.id

  body = {
    properties = {
      resource = {
        id = "workspace"
        partitionKey = {
          paths   = ["/tenantId", "/ownerObjectId", "/sessionId"]
          kind    = "MultiHash"
          version = 2
        }
        indexingPolicy = {
          automatic    = true
          indexingMode = "consistent"
        }
      }
      options = {
        autoscaleSettings = {
          maxThroughput = var.cosmos_max_throughput
        }
      }
    }
  }
}

resource "azapi_resource" "auth" {
  type      = "Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2025-04-15"
  name      = "auth"
  parent_id = azapi_resource.database.id

  body = {
    properties = {
      resource = {
        id = "auth"
        partitionKey = {
          paths = ["/id"]
          kind  = "Hash"
        }
        defaultTtl = -1
      }
    }
  }
}

resource "azapi_resource" "runtime" {
  type      = "Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2025-04-15"
  name      = "runtime"
  parent_id = azapi_resource.database.id

  body = {
    properties = {
      resource = {
        id = "runtime"
        partitionKey = {
          paths = ["/id"]
          kind  = "Hash"
        }
      }
    }
  }
}

resource "azapi_resource" "fabric_auth" {
  count     = var.fabric_enabled ? 1 : 0
  type      = "Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2025-04-15"
  name      = "fabricAuth"
  parent_id = azapi_resource.database.id

  body = {
    properties = {
      resource = {
        id = "fabricAuth"
        partitionKey = {
          paths   = ["/tenantId", "/ownerObjectId"]
          kind    = "MultiHash"
          version = 2
        }
        defaultTtl = -1
      }
    }
  }
}

resource "azapi_resource" "web_cosmos_data_contributor" {
  type      = "Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2025-04-15"
  name      = "${substr(sha1("${azapi_resource.account.id}${var.web_identity_principal_id}${local.cosmos_data_contributor_role}"), 0, 8)}-web"
  parent_id = azapi_resource.account.id

  body = {
    properties = {
      principalId      = var.web_identity_principal_id
      roleDefinitionId = "${azapi_resource.account.id}/sqlRoleDefinitions/${local.cosmos_data_contributor_role}"
      scope            = azapi_resource.account.id
    }
  }
}

resource "azapi_resource" "worker_cosmos_data_contributor" {
  type      = "Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2025-04-15"
  name      = "${substr(sha1("${azapi_resource.account.id}${var.worker_identity_principal_id}${local.cosmos_data_contributor_role}"), 0, 8)}-worker"
  parent_id = azapi_resource.account.id

  body = {
    properties = {
      principalId      = var.worker_identity_principal_id
      roleDefinitionId = "${azapi_resource.account.id}/sqlRoleDefinitions/${local.cosmos_data_contributor_role}"
      scope            = azapi_resource.account.id
    }
  }
}

resource "azapi_resource" "acceptance_cosmos_data_contributor" {
  count     = var.acceptance_principal_id != "" ? 1 : 0
  type      = "Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2025-04-15"
  name      = "${substr(sha1("${azapi_resource.account.id}${var.acceptance_principal_id}${local.cosmos_data_contributor_role}"), 0, 8)}-acceptance"
  parent_id = azapi_resource.account.id

  body = {
    properties = {
      principalId      = var.acceptance_principal_id
      roleDefinitionId = "${azapi_resource.account.id}/sqlRoleDefinitions/${local.cosmos_data_contributor_role}"
      scope            = azapi_resource.account.id
    }
  }
}

resource "azapi_resource" "private_endpoint" {
  count                     = var.profile == "production" ? 1 : 0
  type                      = "Microsoft.Network/privateEndpoints@2024-10-01"
  name                      = "pe-${var.account_name}-sql"
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
          name = "cosmos-sql"
          properties = {
            privateLinkServiceId = azapi_resource.account.id
            groupIds             = ["Sql"]
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
          name = "cosmos-sql"
          properties = {
            privateDnsZoneId = var.private_dns_zone_id
          }
        }
      ]
    }
  }
}

output "account_id" {
  value = azapi_resource.account.id
}

output "endpoint" {
  value = "https://${var.account_name}.documents.azure.com:443/"
}