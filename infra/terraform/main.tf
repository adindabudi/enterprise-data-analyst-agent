provider "azurerm" {
  features {
    resource_group {
      prevent_deletion_if_contains_resources = false
    }
  }
}

provider "azapi" {}

provider "azuread" {}

data "azurerm_client_config" "current" {}

locals {
  tags = {
    "azd-env-name" = var.environment_name
    application    = "enterprise-data-analyst"
    environment    = var.profile
    managedBy      = "terraform"
  }

  compact_environment_name = substr(replace(lower(var.environment_name), "-", ""), 0, 10)
  name_suffix              = substr(var.environment_name, max(length(var.environment_name) - 8, 0), 8)
  names = {
    container_registry = substr("acreda${local.compact_environment_name}${local.name_suffix}", 0, 50)
    cosmos_account     = substr("cosmoseda${local.compact_environment_name}${local.name_suffix}", 0, 44)
    foundry_account    = substr("foundry-eda-${var.environment_name}-${local.name_suffix}-v2", 0, 64)
    key_vault          = substr("kveda${local.compact_environment_name}${local.name_suffix}", 0, 24)
    redis_enterprise   = substr("redis-eda-${var.environment_name}-${local.name_suffix}", 0, 63)
    sandbox_group      = substr("sbg-eda-${var.environment_name}-${local.name_suffix}", 0, 63)
    storage_account    = substr("steda${local.compact_environment_name}${local.name_suffix}", 0, 24)
  }

  fabric_provider_enabled = var.fabric_enabled && var.fabric_provider != ""
  tenant_id               = var.entra_tenant_id != "" ? var.entra_tenant_id : data.azurerm_client_config.current.tenant_id
}

module "resource_group" {
  source   = "./modules/resource_group"
  name     = var.resource_group_name
  location = var.location
  tags     = local.tags
}

module "identities" {
  source              = "./modules/identities"
  location            = var.location
  resource_group_name = module.resource_group.name
  suffix              = local.name_suffix
  subscription_id     = data.azurerm_client_config.current.subscription_id
  tags                = local.tags
}

module "network" {
  source              = "./modules/network"
  environment_name    = var.environment_name
  location            = var.location
  profile             = var.profile
  resource_group_name = module.resource_group.name
  suffix              = local.name_suffix
  subscription_id     = data.azurerm_client_config.current.subscription_id
  tags                = local.tags
}

module "monitoring" {
  source                = "./modules/monitoring"
  environment_name      = var.environment_name
  location              = var.location
  log_retention_in_days = var.log_retention_in_days
  resource_group_name   = module.resource_group.name
  suffix                = local.name_suffix
  subscription_id       = data.azurerm_client_config.current.subscription_id
  tags                  = local.tags
}

module "acr" {
  source                             = "./modules/acr"
  container_registry_name            = local.names.container_registry
  location                           = var.location
  resource_group_name                = module.resource_group.name
  subscription_id                    = data.azurerm_client_config.current.subscription_id
  tags                               = local.tags
  web_identity_principal_id          = module.identities.web_identity_principal_id
  worker_identity_principal_id       = module.identities.worker_identity_principal_id
  session_init_identity_principal_id = module.identities.session_init_identity_principal_id
}

module "storage" {
  source                       = "./modules/storage"
  account_name                 = local.names.storage_account
  location                     = var.location
  profile                      = var.profile
  resource_group_name          = module.resource_group.name
  subscription_id              = data.azurerm_client_config.current.subscription_id
  tags                         = local.tags
  defender_scan_cap_gb         = var.defender_scan_cap_gb
  web_identity_principal_id    = module.identities.web_identity_principal_id
  worker_identity_principal_id = module.identities.worker_identity_principal_id
  acceptance_principal_id      = var.acceptance_principal_id
  private_endpoints_subnet_id  = module.network.private_endpoints_subnet_id
  private_dns_zone_id          = module.network.blob_private_dns_zone_id
}

module "cosmos" {
  source                       = "./modules/cosmos"
  account_name                 = local.names.cosmos_account
  location                     = var.location
  profile                      = var.profile
  resource_group_name          = module.resource_group.name
  subscription_id              = data.azurerm_client_config.current.subscription_id
  tags                         = local.tags
  cosmos_max_throughput        = var.cosmos_autoscale_max_throughput
  web_identity_principal_id    = module.identities.web_identity_principal_id
  worker_identity_principal_id = module.identities.worker_identity_principal_id
  acceptance_principal_id      = var.acceptance_principal_id
  private_endpoints_subnet_id  = module.network.private_endpoints_subnet_id
  private_dns_zone_id          = module.network.cosmos_private_dns_zone_id
  fabric_enabled               = var.fabric_enabled
}

module "redis" {
  source                       = "./modules/redis"
  redis_name                   = local.names.redis_enterprise
  location                     = var.location
  profile                      = var.profile
  redis_sku                    = var.redis_sku
  resource_group_name          = module.resource_group.name
  subscription_id              = data.azurerm_client_config.current.subscription_id
  tags                         = local.tags
  web_identity_principal_id    = module.identities.web_identity_principal_id
  worker_identity_principal_id = module.identities.worker_identity_principal_id
  private_endpoints_subnet_id  = module.network.private_endpoints_subnet_id
  private_dns_zone_id          = module.network.redis_private_dns_zone_id
}

module "foundry" {
  source                       = "./modules/foundry"
  agent_subnet_id              = module.network.foundry_agent_subnet_id
  foundry_name                 = local.names.foundry_account
  location                     = var.location
  resource_group_name          = module.resource_group.name
  subscription_id              = data.azurerm_client_config.current.subscription_id
  tags                         = local.tags
  web_identity_principal_id    = module.identities.web_identity_principal_id
  worker_identity_principal_id = module.identities.worker_identity_principal_id
  model_profile                = var.model_profile
  model_capacity               = var.model_capacity
}

module "fabric_auth" {
  source                       = "./modules/fabric_auth"
  count                        = var.fabric_enabled ? 1 : 0
  enabled                      = var.fabric_enabled
  location                     = var.location
  resource_group_name          = module.resource_group.name
  subscription_id              = data.azurerm_client_config.current.subscription_id
  tenant_id                    = local.tenant_id
  tags                         = local.tags
  vault_name                   = local.names.key_vault
  provisioning_identity_name   = "id-fabric-provision-${local.name_suffix}"
  signing_certificate_name     = var.fabric_signing_certificate_name
  cache_wrap_key_name          = var.fabric_cache_wrap_key_name
  web_identity_principal_id    = module.identities.web_identity_principal_id
  worker_identity_principal_id = module.identities.worker_identity_principal_id
  acceptance_principal_id      = var.acceptance_principal_id
  force_update_tag = sha256(jsonencode({
    profile        = var.profile
    fabric_enabled = var.fabric_enabled
    provider       = var.fabric_provider
    documents      = var.documents_enabled
  }))
}

module "container_apps" {
  source                           = "./modules/container_apps"
  deploy_image_dependent_resources = var.deploy_image_dependent_resources
  api_image                        = var.api_image
  api_max_replicas                 = var.api_max_replicas
  api_min_replicas                 = var.api_min_replicas
  app_insights_connection_string   = module.monitoring.app_insights_connection_string
  aca_subnet_id                    = module.network.aca_subnet_id
  blob_account_url                 = module.storage.blob_endpoint
  container_registry_login_server  = module.acr.login_server
  cosmos_database                  = "enterprise-data-analyst"
  cosmos_endpoint                  = module.cosmos.endpoint
  cosmos_workspace_container       = "workspace"
  entra_client_id                  = var.entra_client_id
  entra_tenant_id                  = var.entra_tenant_id
  environment_name                 = var.environment_name
  resource_group_name              = module.resource_group.name
  foundry_model_deployment         = module.foundry.deployment_name
  foundry_project_endpoint         = module.foundry.project_endpoint
  deployment_id                    = sha256(jsonencode({ env = var.environment_name, profile = var.profile, model = var.model_profile }))
  documents_enabled                = var.documents_enabled
  fabric_enabled                   = var.fabric_enabled
  fabric_provider                  = var.fabric_provider
  fabric_tenant_id                 = var.fabric_tenant_id
  fabric_client_id                 = var.fabric_client_id
  fabric_key_vault_url             = var.fabric_enabled ? module.fabric_auth[0].vault_uri : ""
  fabric_signing_certificate_name  = var.fabric_signing_certificate_name
  fabric_cache_wrap_key_name       = var.fabric_cache_wrap_key_name
  fabric_semantic_models_json      = var.fabric_semantic_models_json
  fabric_ontologies_json           = var.fabric_ontologies_json
  location                         = var.location
  log_analytics_shared_key         = module.monitoring.log_analytics_shared_key
  log_analytics_workspace_id       = module.monitoring.log_analytics_workspace_id
  model_profile                    = var.model_profile
  powerbi_project_enabled          = var.powerbi_project_enabled
  profile                          = var.profile
  redis_url                        = "rediss://${module.redis.hostname}:${module.redis.port}/0"
  subscription_id                  = data.azurerm_client_config.current.subscription_id
  suffix                           = local.name_suffix
  tags                             = local.tags
  web_identity_id                  = module.identities.web_identity_id
  web_identity_client_id           = module.identities.web_identity_client_id
  worker_identity_id               = module.identities.worker_identity_id
  worker_identity_client_id        = module.identities.worker_identity_client_id
  worker_image                     = var.worker_image
  sandbox_image                    = var.sandbox_image
}

module "sandbox_group" {
  source                            = "./modules/sandbox_group"
  count                             = var.deploy_image_dependent_resources ? 1 : 0
  location                          = var.location
  provisioning_principal_id         = var.principal_id
  resource_group_name               = module.resource_group.name
  sandbox_group_name                = local.names.sandbox_group
  sandbox_subnet_id                 = module.network.sandbox_subnet_id
  session_init_identity_resource_id = module.identities.session_init_identity_id
  subscription_id                   = data.azurerm_client_config.current.subscription_id
  tags                              = local.tags
  web_identity_principal_id         = module.identities.web_identity_principal_id
}

module "budgets_alerts" {
  source                        = "./modules/budgets_alerts"
  count                         = var.monitoring_alerts_enabled ? 1 : 0
  action_group_name             = var.budget_action_group_name
  alert_email                   = var.budget_alert_email
  api_app_id                    = module.container_apps.api_id
  app_insights_id               = module.monitoring.app_insights_id
  container_apps_environment_id = module.container_apps.environment_id
  cosmos_account_id             = module.cosmos.account_id
  location                      = var.location
  monthly_budget_amount         = var.monthly_budget_amount
  resource_group_name           = module.resource_group.name
  redis_cluster_id              = module.redis.cluster_id
  subscription_id               = data.azurerm_client_config.current.subscription_id
  tags                          = local.tags
}
