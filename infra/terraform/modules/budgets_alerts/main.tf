variable "action_group_name" {
  type = string
}

variable "alert_email" {
  type = string
}

variable "api_app_id" {
  type = string
}

variable "app_insights_id" {
  type = string
}

variable "container_apps_environment_id" {
  type = string
}

variable "cosmos_account_id" {
  type = string
}

variable "location" {
  type = string
}

variable "monthly_budget_amount" {
  type = number
}

variable "redis_cluster_id" {
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

locals {
  resource_group_id = "/subscriptions/${var.subscription_id}/resourceGroups/${var.resource_group_name}"
  runbook_base_url  = "https://github.com/Azure/enterprise-data-analyst/tree/main/docs/runbooks"
}

resource "azapi_resource" "action_group" {
  type                      = "Microsoft.Insights/actionGroups@2023-01-01"
  name                      = var.action_group_name
  location                  = "global"
  parent_id                 = local.resource_group_id
  schema_validation_enabled = false
  tags                      = var.tags

  body = {
    properties = {
      enabled        = true
      groupShortName = substr(var.action_group_name, 0, 12)
      emailReceivers = [{ name = "operations", emailAddress = var.alert_email, useCommonAlertSchema = true }]
    }
  }
}

resource "azapi_resource" "monthly_budget" {
  type                      = "Microsoft.Consumption/budgets@2024-08-01"
  name                      = "monthly-operations"
  parent_id                 = local.resource_group_id
  schema_validation_enabled = false

  body = {
    properties = {
      category   = "Cost"
      amount     = var.monthly_budget_amount
      timeGrain  = "Monthly"
      timePeriod = { startDate = "2026-01-01T00:00:00Z", endDate = "2036-01-01T00:00:00Z" }
      notifications = {
        actual50     = { enabled = true, operator = "GreaterThan", threshold = 50, contactGroups = [azapi_resource.action_group.id], contactEmails = [var.alert_email] }
        actual75     = { enabled = true, operator = "GreaterThan", threshold = 75, contactGroups = [azapi_resource.action_group.id], contactEmails = [var.alert_email] }
        actual90     = { enabled = true, operator = "GreaterThan", threshold = 90, contactGroups = [azapi_resource.action_group.id], contactEmails = [var.alert_email] }
        actual100    = { enabled = true, operator = "GreaterThan", threshold = 100, contactGroups = [azapi_resource.action_group.id], contactEmails = [var.alert_email] }
        forecasted90 = { enabled = true, operator = "GreaterThan", threshold = 90, thresholdType = "Forecasted", contactGroups = [azapi_resource.action_group.id], contactEmails = [var.alert_email] }
      }
    }
  }
}

resource "azapi_resource" "api_5xx" {
  type                      = "Microsoft.Insights/metricAlerts@2018-03-01"
  name                      = "api-5xx"
  location                  = "global"
  parent_id                 = local.resource_group_id
  schema_validation_enabled = false
  tags                      = merge(var.tags, { runbook = "${local.runbook_base_url}/rollback.md" })
  body                      = { properties = { scopes = [var.api_app_id] } }
}

resource "azapi_resource" "api_p95" {
  type                      = "Microsoft.Insights/scheduledQueryRules@2026-03-01"
  name                      = "api-p95"
  location                  = var.location
  parent_id                 = local.resource_group_id
  schema_validation_enabled = false
  tags                      = merge(var.tags, { runbook = "${local.runbook_base_url}/rollback.md" })
  body                      = { kind = "LogAlert", properties = { scopes = [var.app_insights_id] } }
}

resource "azapi_resource" "worker_task_failed" {
  type                      = "Microsoft.Insights/scheduledQueryRules@2026-03-01"
  name                      = "worker-task-failed"
  location                  = var.location
  parent_id                 = local.resource_group_id
  schema_validation_enabled = false
  tags                      = merge(var.tags, { runbook = "${local.runbook_base_url}/rollback.md" })
  body                      = { kind = "LogAlert", properties = { scopes = [var.app_insights_id] } }
}

resource "azapi_resource" "cancel_ack" {
  type                      = "Microsoft.Insights/scheduledQueryRules@2026-03-01"
  name                      = "cancel-ack"
  location                  = var.location
  parent_id                 = local.resource_group_id
  schema_validation_enabled = false
  tags                      = merge(var.tags, { runbook = "${local.runbook_base_url}/rollback.md" })
  body                      = { kind = "LogAlert", properties = { scopes = [var.app_insights_id] } }
}

resource "azapi_resource" "redis_circuit_open" {
  type                      = "Microsoft.Insights/metricAlerts@2018-03-01"
  name                      = "redis-circuit-open"
  location                  = "global"
  parent_id                 = local.resource_group_id
  schema_validation_enabled = false
  tags                      = merge(var.tags, { runbook = "${local.runbook_base_url}/redis-outage.md" })
  body                      = { properties = { scopes = [var.redis_cluster_id] } }
}

resource "azapi_resource" "cosmos_429" {
  type                      = "Microsoft.Insights/metricAlerts@2018-03-01"
  name                      = "cosmos-429"
  location                  = "global"
  parent_id                 = local.resource_group_id
  schema_validation_enabled = false
  tags                      = merge(var.tags, { runbook = "${local.runbook_base_url}/storage-restore.md" })
  body                      = { properties = { scopes = [var.cosmos_account_id] } }
}

resource "azapi_resource" "session_oom" {
  type                      = "Microsoft.Insights/scheduledQueryRules@2026-03-01"
  name                      = "session-oom"
  location                  = var.location
  parent_id                 = local.resource_group_id
  schema_validation_enabled = false
  body                      = { kind = "LogAlert", properties = { scopes = [var.app_insights_id] } }
}

resource "azapi_resource" "session_capacity" {
  type                      = "Microsoft.Insights/metricAlerts@2018-03-01"
  name                      = "session-capacity"
  location                  = "global"
  parent_id                 = local.resource_group_id
  schema_validation_enabled = false
  body                      = { properties = { scopes = [var.container_apps_environment_id] } }
}

resource "azapi_resource" "session_stop" {
  type                      = "Microsoft.Insights/scheduledQueryRules@2026-03-01"
  name                      = "session-stop"
  location                  = var.location
  parent_id                 = local.resource_group_id
  schema_validation_enabled = false
  body                      = { kind = "LogAlert", properties = { scopes = [var.app_insights_id] } }
}

resource "azapi_resource" "worker_ready_replicas" {
  type                      = "Microsoft.Insights/metricAlerts@2018-03-01"
  name                      = "worker-ready-replicas"
  location                  = "global"
  parent_id                 = local.resource_group_id
  schema_validation_enabled = false
  body                      = { properties = { scopes = [var.api_app_id] } }
}

output "action_group_id" {
  value = azapi_resource.action_group.id
}