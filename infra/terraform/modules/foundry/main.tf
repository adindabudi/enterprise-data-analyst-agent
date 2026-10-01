variable "foundry_name" {
  type = string
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

variable "agent_subnet_id" {
  type = string
}

variable "tags" {
  type = map(string)
}

variable "web_identity_principal_id" {
  type = string
}

variable "model_profile" {
  type = string
}

variable "model_capacity" {
  type = number
}

locals {
  resource_group_id            = "/subscriptions/${var.subscription_id}/resourceGroups/${var.resource_group_name}"
  project_name                 = "eda-project"
  deployment_name              = "gpt-5.6-terra"
  cognitive_services_user_role = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/a97b65f3-24c7-4388-baec-2e87135dc908"
}

resource "azapi_resource" "account" {
  type                      = "Microsoft.CognitiveServices/accounts@2025-10-01-preview"
  name                      = var.foundry_name
  location                  = var.location
  parent_id                 = local.resource_group_id
  schema_validation_enabled = false
  tags                      = merge(var.tags, { modelProfile = var.model_profile })

  body = {
    kind = "AIServices"
    sku = {
      name = "S0"
    }
    identity = {
      type = "SystemAssigned"
    }
    properties = {
      customSubDomainName    = var.foundry_name
      allowProjectManagement = true
      publicNetworkAccess    = "Enabled"
      disableLocalAuth       = true
      networkInjections = [
        {
          scenario                   = "agent"
          subnetArmId                = var.agent_subnet_id
          useMicrosoftManagedNetwork = false
        }
      ]
    }
  }
}

resource "azapi_resource" "project" {
  type                      = "Microsoft.CognitiveServices/accounts/projects@2025-10-01-preview"
  name                      = local.project_name
  location                  = var.location
  parent_id                 = azapi_resource.account.id
  schema_validation_enabled = false

  body = {
    identity = {
      type = "SystemAssigned"
    }
    properties = {}
  }
}

resource "azapi_resource" "terra" {
  type                      = "Microsoft.CognitiveServices/accounts/deployments@2025-10-01-preview"
  name                      = local.deployment_name
  parent_id                 = azapi_resource.account.id
  schema_validation_enabled = false

  body = {
    sku = {
      name     = "GlobalStandard"
      capacity = var.model_capacity
    }
    properties = {
      model = {
        format  = "OpenAI"
        name    = "gpt-5.6-terra"
        version = "2026-07-09"
      }
      versionUpgradeOption = "NoAutoUpgrade"
      raiPolicyName        = "Microsoft.DefaultV2"
    }
  }
}

resource "azurerm_role_assignment" "web_cognitive_services_user" {
  principal_id       = var.web_identity_principal_id
  principal_type     = "ServicePrincipal"
  role_definition_id = local.cognitive_services_user_role
  scope              = azapi_resource.account.id
}

output "account_id" {
  value = azapi_resource.account.id
}

output "project_endpoint" {
  value = "https://${var.foundry_name}.services.ai.azure.com/api/projects/${local.project_name}"
}

output "deployment_name" {
  value = local.deployment_name
}

output "model_profile" {
  value = var.model_profile
}