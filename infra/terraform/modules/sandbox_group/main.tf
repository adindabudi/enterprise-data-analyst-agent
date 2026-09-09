variable "location" {
  type = string
}

variable "provisioning_principal_id" {
  type = string
}

variable "resource_group_name" {
  type = string
}

variable "sandbox_group_name" {
  type = string
}

variable "sandbox_subnet_id" {
  type = string
}

variable "session_init_identity_resource_id" {
  type = string
}

variable "subscription_id" {
  type = string
}

variable "tags" {
  type = map(string)
}

locals {
  resource_group_id          = "/subscriptions/${var.subscription_id}/resourceGroups/${var.resource_group_name}"
  sandbox_data_owner_role_id = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/c24cf47c-5077-412d-a19c-45202126392c"
}

resource "azapi_resource" "group" {
  type                      = "Microsoft.App/sandboxGroups@2026-02-01-preview"
  name                      = var.sandbox_group_name
  location                  = var.location
  parent_id                 = local.resource_group_id
  schema_validation_enabled = false
  tags                      = var.tags

  body = {
    identity = {
      type = "UserAssigned"
      userAssignedIdentities = {
        "${var.session_init_identity_resource_id}" = {}
      }
    }
    properties = {
      defaultCpu            = "2"
      defaultMemory         = "4Gi"
      defaultDisk           = "40Gi"
      maxSandboxCount       = 10
      defaultTimeoutSeconds = 300
    }
  }
}

resource "azapi_resource" "vnet_connection" {
  type                      = "Microsoft.App/sandboxGroups/vnetConnections@2026-02-01-preview"
  name                      = "default"
  parent_id                 = azapi_resource.group.id
  location                  = var.location
  schema_validation_enabled = false

  body = {
    properties = {
      subnetId = var.sandbox_subnet_id
    }
  }
}

resource "azurerm_role_assignment" "provisioning_sandbox_data_owner" {
  principal_id       = var.provisioning_principal_id
  role_definition_id = local.sandbox_data_owner_role_id
  scope              = azapi_resource.group.id
}

output "sandbox_group_id" {
  value = azapi_resource.group.id
}
