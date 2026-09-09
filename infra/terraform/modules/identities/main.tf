variable "location" {
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

resource "azurerm_user_assigned_identity" "web" {
  location            = var.location
  name                = "id-eda-web-${var.suffix}"
  resource_group_name = var.resource_group_name
  tags                = var.tags
}

resource "azurerm_user_assigned_identity" "worker" {
  location            = var.location
  name                = "id-eda-worker-${var.suffix}"
  resource_group_name = var.resource_group_name
  tags                = var.tags
}

resource "azurerm_user_assigned_identity" "session_init" {
  location            = var.location
  name                = "id-eda-session-init-${var.suffix}"
  resource_group_name = var.resource_group_name
  tags                = var.tags
}

output "web_identity_id" {
  value = azurerm_user_assigned_identity.web.id
}

output "web_identity_client_id" {
  value = azurerm_user_assigned_identity.web.client_id
}

output "web_identity_principal_id" {
  value = azurerm_user_assigned_identity.web.principal_id
}

output "worker_identity_id" {
  value = azurerm_user_assigned_identity.worker.id
}

output "worker_identity_client_id" {
  value = azurerm_user_assigned_identity.worker.client_id
}

output "worker_identity_principal_id" {
  value = azurerm_user_assigned_identity.worker.principal_id
}

output "session_init_identity_id" {
  value = azurerm_user_assigned_identity.session_init.id
}

output "session_init_identity_client_id" {
  value = azurerm_user_assigned_identity.session_init.client_id
}

output "session_init_identity_principal_id" {
  value = azurerm_user_assigned_identity.session_init.principal_id
}