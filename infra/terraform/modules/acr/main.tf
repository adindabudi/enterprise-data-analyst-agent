variable "container_registry_name" {
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

variable "tags" {
  type = map(string)
}

variable "web_identity_principal_id" {
  type = string
}

variable "worker_identity_principal_id" {
  type = string
}

variable "session_init_identity_principal_id" {
  type = string
}

locals {
  acr_pull_role_definition_id = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/7f951dda-4ed3-4680-a7ca-43fe172d538d"
}

resource "azurerm_container_registry" "this" {
  admin_enabled       = false
  location            = var.location
  name                = var.container_registry_name
  resource_group_name = var.resource_group_name
  sku                 = "Standard"
  tags                = var.tags
}

resource "azurerm_role_assignment" "web_pull" {
  principal_id       = var.web_identity_principal_id
  principal_type     = "ServicePrincipal"
  role_definition_id = local.acr_pull_role_definition_id
  scope              = azurerm_container_registry.this.id
}

resource "azurerm_role_assignment" "worker_pull" {
  principal_id       = var.worker_identity_principal_id
  principal_type     = "ServicePrincipal"
  role_definition_id = local.acr_pull_role_definition_id
  scope              = azurerm_container_registry.this.id
}

resource "azurerm_role_assignment" "session_init_pull" {
  principal_id       = var.session_init_identity_principal_id
  principal_type     = "ServicePrincipal"
  role_definition_id = local.acr_pull_role_definition_id
  scope              = azurerm_container_registry.this.id
}

output "registry_id" {
  value = azurerm_container_registry.this.id
}

output "login_server" {
  value = azurerm_container_registry.this.login_server
}