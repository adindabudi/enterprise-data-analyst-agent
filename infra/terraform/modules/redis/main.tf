variable "redis_name" {
  type = string
}

variable "location" {
  type = string
}

variable "profile" {
  type = string
}

variable "redis_sku" {
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

variable "web_identity_principal_id" {
  type = string
}

variable "worker_identity_principal_id" {
  type = string
}

variable "private_endpoints_subnet_id" {
  type = string
}

variable "private_dns_zone_id" {
  type = string
}

locals {
  resource_group_id = "/subscriptions/${var.subscription_id}/resourceGroups/${var.resource_group_name}"
}

resource "azapi_resource" "cluster" {
  type                      = "Microsoft.Cache/redisEnterprise@2025-07-01"
  name                      = var.redis_name
  location                  = var.location
  parent_id                 = local.resource_group_id
  schema_validation_enabled = false
  tags                      = var.tags

  body = {
    sku = {
      name = var.redis_sku
    }
    properties = {
      encryption          = {}
      highAvailability    = var.profile == "production" ? "Enabled" : "Disabled"
      minimumTlsVersion   = "1.2"
      publicNetworkAccess = var.profile == "production" ? "Disabled" : "Enabled"
    }
  }
}

resource "azapi_resource" "database" {
  type                      = "Microsoft.Cache/redisEnterprise/databases@2025-07-01"
  name                      = "default"
  parent_id                 = azapi_resource.cluster.id
  schema_validation_enabled = false

  body = {
    properties = {
      accessKeysAuthentication = "Disabled"
      clientProtocol           = "Encrypted"
      clusteringPolicy         = "OSSCluster"
      evictionPolicy           = "VolatileLRU"
      port                     = 10000
    }
  }
}

resource "azapi_resource" "web_access_policy_assignment" {
  type                      = "Microsoft.Cache/redisEnterprise/databases/accessPolicyAssignments@2025-07-01"
  name                      = "web"
  parent_id                 = azapi_resource.database.id
  schema_validation_enabled = false

  body = {
    properties = {
      accessPolicyName = "default"
      user = {
        objectId = var.web_identity_principal_id
      }
    }
  }
}

resource "azapi_resource" "worker_access_policy_assignment" {
  type                      = "Microsoft.Cache/redisEnterprise/databases/accessPolicyAssignments@2025-07-01"
  name                      = "worker"
  parent_id                 = azapi_resource.database.id
  schema_validation_enabled = false

  body = {
    properties = {
      accessPolicyName = "default"
      user = {
        objectId = var.worker_identity_principal_id
      }
    }
  }
}

resource "azapi_resource" "private_endpoint" {
  count                     = var.profile == "production" ? 1 : 0
  type                      = "Microsoft.Network/privateEndpoints@2024-10-01"
  name                      = "pe-${var.redis_name}"
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
          name = "redis"
          properties = {
            privateLinkServiceId = azapi_resource.cluster.id
            groupIds             = ["redisEnterprise"]
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
          name = "redis"
          properties = {
            privateDnsZoneId = var.private_dns_zone_id
          }
        }
      ]
    }
  }
}

output "cluster_id" {
  value = azapi_resource.cluster.id
}

output "hostname" {
  value = "${var.redis_name}.${var.location}.redis.azure.net"
}

output "port" {
  value = 10000
}