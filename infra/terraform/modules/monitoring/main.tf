variable "environment_name" {
  type = string
}

variable "location" {
  type = string
}

variable "log_retention_in_days" {
  type = number
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

resource "azurerm_log_analytics_workspace" "this" {
  location            = var.location
  name                = "law-eda-${var.environment_name}-${var.suffix}"
  resource_group_name = var.resource_group_name
  retention_in_days   = var.log_retention_in_days
  sku                 = "PerGB2018"
  tags                = var.tags
}

resource "azurerm_application_insights" "this" {
  application_type    = "web"
  location            = var.location
  name                = "appi-eda-${var.environment_name}-${var.suffix}"
  resource_group_name = var.resource_group_name
  retention_in_days   = var.log_retention_in_days
  sampling_percentage = 100
  workspace_id        = azurerm_log_analytics_workspace.this.id
  tags                = var.tags
}

output "workspace_id" {
  value = azurerm_log_analytics_workspace.this.id
}

output "log_analytics_workspace_id" {
  value = azurerm_log_analytics_workspace.this.workspace_id
}

output "log_analytics_shared_key" {
  value     = azurerm_log_analytics_workspace.this.primary_shared_key
  sensitive = true
}

output "app_insights_id" {
  value = azurerm_application_insights.this.id
}

output "app_insights_connection_string" {
  value     = azurerm_application_insights.this.connection_string
  sensitive = true
}