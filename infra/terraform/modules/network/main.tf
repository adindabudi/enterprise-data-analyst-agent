variable "environment_name" {
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

variable "suffix" {
  type = string
}

variable "tags" {
  type = map(string)
}

locals {
  virtual_network_name = "vnet-eda-${var.environment_name}-${var.suffix}"
}

resource "azurerm_virtual_network" "this" {
  address_space       = ["10.42.0.0/16"]
  location            = var.location
  name                = local.virtual_network_name
  resource_group_name = var.resource_group_name
  tags                = var.tags
}

resource "azurerm_subnet" "aca" {
  address_prefixes     = ["10.42.0.0/23"]
  name                 = "aca"
  resource_group_name  = var.resource_group_name
  virtual_network_name = azurerm_virtual_network.this.name

  delegation {
    name = "container-apps-environment"

    service_delegation {
      actions = ["Microsoft.Network/virtualNetworks/subnets/action"]
      name    = "Microsoft.App/environments"
    }
  }
}

resource "azurerm_subnet" "private_endpoints" {
  address_prefixes                  = ["10.42.2.0/24"]
  name                              = "private-endpoints"
  private_endpoint_network_policies = "Disabled"
  resource_group_name               = var.resource_group_name
  virtual_network_name              = azurerm_virtual_network.this.name
}

resource "azurerm_subnet" "sandboxes" {
  address_prefixes     = ["10.42.4.0/23"]
  name                 = "sandboxes"
  resource_group_name  = var.resource_group_name
  virtual_network_name = azurerm_virtual_network.this.name

  delegation {
    name = "sandbox-groups"

    service_delegation {
      actions = ["Microsoft.Network/virtualNetworks/subnets/action"]
      name    = "Microsoft.App/environments"
    }
  }
}

resource "azurerm_subnet" "foundry_agents" {
  address_prefixes     = ["10.42.7.0/24"]
  name                 = "foundry-agents-v2"
  resource_group_name  = var.resource_group_name
  virtual_network_name = azurerm_virtual_network.this.name

  delegation {
    name = "foundry-agent-environment"

    service_delegation {
      actions = ["Microsoft.Network/virtualNetworks/subnets/action"]
      name    = "Microsoft.App/environments"
    }
  }
}

resource "azurerm_private_dns_zone" "blob" {
  count               = var.profile == "production" ? 1 : 0
  name                = "privatelink.blob.core.windows.net"
  resource_group_name = var.resource_group_name
  tags                = var.tags
}

resource "azurerm_private_dns_zone" "cosmos" {
  count               = var.profile == "production" ? 1 : 0
  name                = "privatelink.documents.azure.com"
  resource_group_name = var.resource_group_name
  tags                = var.tags
}

resource "azurerm_private_dns_zone" "redis" {
  count               = var.profile == "production" ? 1 : 0
  name                = "privatelink.redis.azure.net"
  resource_group_name = var.resource_group_name
  tags                = var.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "blob" {
  count                 = var.profile == "production" ? 1 : 0
  name                  = local.virtual_network_name
  private_dns_zone_name = azurerm_private_dns_zone.blob[0].name
  registration_enabled  = false
  resource_group_name   = var.resource_group_name
  virtual_network_id    = azurerm_virtual_network.this.id
  tags                  = var.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "cosmos" {
  count                 = var.profile == "production" ? 1 : 0
  name                  = local.virtual_network_name
  private_dns_zone_name = azurerm_private_dns_zone.cosmos[0].name
  registration_enabled  = false
  resource_group_name   = var.resource_group_name
  virtual_network_id    = azurerm_virtual_network.this.id
  tags                  = var.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "redis" {
  count                 = var.profile == "production" ? 1 : 0
  name                  = local.virtual_network_name
  private_dns_zone_name = azurerm_private_dns_zone.redis[0].name
  registration_enabled  = false
  resource_group_name   = var.resource_group_name
  virtual_network_id    = azurerm_virtual_network.this.id
  tags                  = var.tags
}

output "virtual_network_id" {
  value = azurerm_virtual_network.this.id
}

output "aca_subnet_id" {
  value = azurerm_subnet.aca.id
}

output "sandbox_subnet_id" {
  value = azurerm_subnet.sandboxes.id
}

output "foundry_agent_subnet_id" {
  value = azurerm_subnet.foundry_agents.id
}

output "private_endpoints_subnet_id" {
  value = azurerm_subnet.private_endpoints.id
}

output "blob_private_dns_zone_id" {
  value = var.profile == "production" ? azurerm_private_dns_zone.blob[0].id : ""
}

output "cosmos_private_dns_zone_id" {
  value = var.profile == "production" ? azurerm_private_dns_zone.cosmos[0].id : ""
}

output "redis_private_dns_zone_id" {
  value = var.profile == "production" ? azurerm_private_dns_zone.redis[0].id : ""
}