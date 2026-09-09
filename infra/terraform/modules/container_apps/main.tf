variable "deploy_image_dependent_resources" {
  type = bool
}

variable "api_image" {
  type = string
}

variable "api_max_replicas" {
  type = number
}

variable "api_min_replicas" {
  type = number
}

variable "app_insights_connection_string" {
  type      = string
  sensitive = true
}

variable "aca_subnet_id" {
  type = string
}

variable "blob_account_url" {
  type = string
}

variable "container_registry_login_server" {
  type = string
}

variable "cosmos_database" {
  type = string
}

variable "cosmos_endpoint" {
  type = string
}

variable "cosmos_workspace_container" {
  type = string
}

variable "entra_client_id" {
  type = string
}

variable "entra_tenant_id" {
  type = string
}

variable "environment_name" {
  type = string
}

variable "resource_group_name" {
  type = string
}

variable "foundry_model_deployment" {
  type = string
}

variable "foundry_project_endpoint" {
  type = string
}

variable "deployment_id" {
  type = string
}

variable "documents_enabled" {
  type = bool
}

variable "fabric_enabled" {
  type = bool
}

variable "fabric_provider" {
  type = string
}

variable "fabric_tenant_id" {
  type = string
}

variable "fabric_client_id" {
  type = string
}

variable "fabric_key_vault_url" {
  type = string
}

variable "fabric_signing_certificate_name" {
  type = string
}

variable "fabric_cache_wrap_key_name" {
  type = string
}

variable "fabric_semantic_models_json" {
  type = string
}

variable "fabric_ontologies_json" {
  type = string
}

variable "location" {
  type = string
}

variable "log_analytics_shared_key" {
  type      = string
  sensitive = true
}

variable "log_analytics_workspace_id" {
  type = string
}

variable "model_profile" {
  type = string
}

variable "powerbi_project_enabled" {
  type = bool
}

variable "profile" {
  type = string
}

variable "redis_url" {
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

variable "web_identity_id" {
  type = string
}

variable "web_identity_client_id" {
  type = string
}

variable "worker_identity_id" {
  type = string
}

variable "worker_identity_client_id" {
  type = string
}

variable "worker_image" {
  type = string
}

variable "sandbox_image" {
  type     = string
  nullable = true
}

locals {
  resource_group_id     = "/subscriptions/${var.subscription_id}/resourceGroups/${var.resource_group_name}"
  environment_name_full = "cae-eda-${var.environment_name}-${var.suffix}"
  api_name              = "api-eda-${var.environment_name}-${var.suffix}"
  cleanup_name          = "cleanup-eda-${var.environment_name}-${var.suffix}"
  acceptance_name       = "fabric-acc-${var.environment_name}-${var.suffix}"
}

resource "azapi_resource" "environment" {
  type                      = "Microsoft.App/managedEnvironments@2026-01-01"
  name                      = local.environment_name_full
  location                  = var.location
  parent_id                 = local.resource_group_id
  schema_validation_enabled = false
  tags                      = var.tags

  body = {
    properties = {
      vnetConfiguration = {
        infrastructureSubnetId = var.aca_subnet_id
      }
      zoneRedundant = var.profile == "production"
      appLogsConfiguration = {
        destination = "log-analytics"
        logAnalyticsConfiguration = {
          customerId = var.log_analytics_workspace_id
          sharedKey  = var.log_analytics_shared_key
        }
      }
    }
  }
}

resource "azapi_resource" "api" {
  type                      = "Microsoft.App/containerApps@2026-01-01"
  name                      = local.api_name
  location                  = var.location
  parent_id                 = local.resource_group_id
  schema_validation_enabled = false
  tags                      = merge(var.tags, { "azd-service-name" = "api" })
  response_export_values    = ["properties.configuration.ingress.fqdn"]

  body = {
    identity = {
      type = "UserAssigned"
      userAssignedIdentities = {
        "${var.web_identity_id}" = {}
      }
    }
    properties = {
      managedEnvironmentId = azapi_resource.environment.id
      workloadProfileName  = "Consumption"
      configuration = {
        activeRevisionsMode = "Single"
        registries          = [{ server = var.container_registry_login_server, identity = var.web_identity_id }]
        secrets             = [{ name = "appinsights-connection-string", value = var.app_insights_connection_string }]
        ingress = {
          external       = true
          targetPort     = 8000
          transport      = "auto"
          stickySessions = { affinity = "none" }
        }
      }
      template = {
        scale = {
          minReplicas = var.api_min_replicas
          maxReplicas = var.api_max_replicas
        }
        containers = [
          {
            name      = "api"
            image     = var.api_image
            resources = { cpu = 1, memory = "2Gi" }
            env = concat([
              { name = "APPLICATIONINSIGHTS_CONNECTION_STRING", secretRef = "appinsights-connection-string" },
              { name = "EDA_APP_ENV", value = "production" },
              { name = "EDA_MANAGED_IDENTITY_CLIENT_ID", value = var.web_identity_client_id },
              { name = "EDA_COSMOS_ENDPOINT", value = var.cosmos_endpoint },
              { name = "EDA_COSMOS_DATABASE", value = var.cosmos_database },
              { name = "EDA_COSMOS_WORKSPACE_CONTAINER", value = var.cosmos_workspace_container },
              { name = "EDA_COSMOS_AUTH_CONTAINER", value = "auth" },
              { name = "EDA_COSMOS_RUNTIME_CONTAINER", value = "runtime" },
              { name = "EDA_BLOB_ACCOUNT_URL", value = var.blob_account_url },
              { name = "EDA_BLOB_QUARANTINE_CONTAINER", value = "quarantine" },
              { name = "EDA_BLOB_SESSIONS_CONTAINER", value = "sessions" },
              { name = "EDA_REDIS_URL", value = var.redis_url },
              { name = "EDA_FOUNDRY_PROJECT_ENDPOINT", value = var.foundry_project_endpoint },
              { name = "EDA_FOUNDRY_MODEL_DEPLOYMENT", value = var.foundry_model_deployment },
              { name = "EDA_MODEL_PROFILE", value = var.model_profile },
              { name = "EDA_FOUNDRY_HOSTING", value = "azure" },
              { name = "EDA_PUBLIC_ORIGIN", value = "https://${local.api_name}.azurecontainerapps.io" },
              { name = "EDA_ENTRA_TENANT_ID", value = var.entra_tenant_id },
              { name = "EDA_ENTRA_CLIENT_ID", value = var.entra_client_id },
              { name = "EDA_COOKIE_SECURE", value = "true" },
              { name = "EDA_DEPLOYMENT_ID", value = var.deployment_id },
              { name = "EDA_WORKER_IMAGE_DIGEST", value = element(split("@", var.worker_image), 1) },
              { name = "EDA_COSMOS_FABRIC_AUTH_CONTAINER", value = "fabricAuth" },
              { name = "DOCUMENTS_ENABLED", value = tostring(var.documents_enabled) },
              { name = "POWERBI_PROJECT_ENABLED", value = tostring(var.powerbi_project_enabled) },
              ], var.sandbox_image != null ? [
              { name = "EDA_SANDBOX_IMAGE_DIGEST", value = element(split("@", var.sandbox_image), 1) },
              ] : [], var.fabric_enabled ? [
              { name = "FABRIC_ENABLED", value = "true" },
              { name = "FABRIC_PROVIDER", value = var.fabric_provider },
              { name = "FABRIC_TENANT_ID", value = var.fabric_tenant_id },
              { name = "FABRIC_CLIENT_ID", value = var.fabric_client_id },
              { name = "FABRIC_KEY_VAULT_URL", value = var.fabric_key_vault_url },
              { name = "FABRIC_SIGNING_CERTIFICATE_NAME", value = var.fabric_signing_certificate_name },
              { name = "FABRIC_CACHE_WRAP_KEY_NAME", value = var.fabric_cache_wrap_key_name },
            ] : [])
            probes = [
              { type = "Startup", httpGet = { path = "/health/platform-ready", port = 8000 }, periodSeconds = 5, failureThreshold = 30 },
              { type = "Readiness", httpGet = { path = "/health/platform-ready", port = 8000 }, periodSeconds = 10, failureThreshold = 3 },
              { type = "Liveness", httpGet = { path = "/health/live", port = 8000 }, periodSeconds = 10, failureThreshold = 3 },
            ]
          }
        ]
      }
    }
  }
}

resource "azapi_resource" "cleanup" {
  count                     = var.deploy_image_dependent_resources ? 1 : 0
  type                      = "Microsoft.App/jobs@2026-01-01"
  name                      = local.cleanup_name
  location                  = var.location
  parent_id                 = local.resource_group_id
  schema_validation_enabled = false
  tags                      = var.tags

  body = {
    identity = {
      type = "UserAssigned"
      userAssignedIdentities = {
        "${var.worker_identity_id}" = {}
      }
    }
    properties = {
      environmentId = azapi_resource.environment.id
      configuration = {
        triggerType       = "Schedule"
        replicaTimeout    = 1800
        replicaRetryLimit = 2
        scheduleTriggerConfig = {
          cronExpression         = "0 2 * * *"
          parallelism            = 1
          replicaCompletionCount = 1
        }
        registries = [{ server = var.container_registry_login_server, identity = var.worker_identity_id }]
        secrets    = [{ name = "appinsights-connection-string", value = var.app_insights_connection_string }]
      }
      template = {
        containers = [
          {
            name      = "cleanup"
            image     = var.worker_image
            command   = ["eda-worker", "cleanup", "--before", "now", "--limit", "100"]
            resources = { cpu = 1, memory = "2Gi" }
            env = [
              { name = "APPLICATIONINSIGHTS_CONNECTION_STRING", secretRef = "appinsights-connection-string" },
              { name = "EDA_MANAGED_IDENTITY_CLIENT_ID", value = var.worker_identity_client_id },
              { name = "EDA_COSMOS_ENDPOINT", value = var.cosmos_endpoint },
              { name = "EDA_COSMOS_DATABASE", value = var.cosmos_database },
              { name = "EDA_COSMOS_WORKSPACE_CONTAINER", value = var.cosmos_workspace_container },
              { name = "EDA_BLOB_ACCOUNT_URL", value = var.blob_account_url },
              { name = "EDA_BLOB_SESSIONS_CONTAINER", value = "sessions" },
            ]
          }
        ]
      }
    }
  }
}

resource "azapi_resource" "fabric_acceptance" {
  count                     = var.deploy_image_dependent_resources && var.fabric_enabled ? 1 : 0
  type                      = "Microsoft.App/jobs@2026-01-01"
  name                      = local.acceptance_name
  location                  = var.location
  parent_id                 = local.resource_group_id
  schema_validation_enabled = false
  tags                      = var.tags

  body = {
    identity = {
      type = "UserAssigned"
      userAssignedIdentities = {
        "${var.worker_identity_id}" = {}
      }
    }
    properties = {
      environmentId = azapi_resource.environment.id
      configuration = {
        triggerType       = "Manual"
        replicaTimeout    = 1800
        replicaRetryLimit = 0
        manualTriggerConfig = {
          parallelism            = 1
          replicaCompletionCount = 1
        }
        registries = [{ server = var.container_registry_login_server, identity = var.worker_identity_id }]
        secrets    = [{ name = "appinsights-connection-string", value = var.app_insights_connection_string }]
      }
      template = {
        containers = [
          {
            name      = "fabric-acceptance"
            image     = var.worker_image
            command   = ["eda-worker", "accept-fabric", "--provider", var.fabric_provider]
            resources = { cpu = 1, memory = "2Gi" }
            env = concat([
              { name = "APPLICATIONINSIGHTS_CONNECTION_STRING", secretRef = "appinsights-connection-string" },
              { name = "FABRIC_ACCEPTANCE_MODE", value = "true" },
              { name = "EDA_APP_ENV", value = "production" },
              { name = "EDA_DEPLOYMENT_ID", value = var.deployment_id },
              { name = "EDA_MANAGED_IDENTITY_CLIENT_ID", value = var.worker_identity_client_id },
              { name = "EDA_COSMOS_ENDPOINT", value = var.cosmos_endpoint },
              { name = "EDA_COSMOS_DATABASE", value = var.cosmos_database },
              { name = "EDA_COSMOS_WORKSPACE_CONTAINER", value = var.cosmos_workspace_container },
              { name = "EDA_COSMOS_AUTH_CONTAINER", value = "auth" },
              { name = "EDA_COSMOS_RUNTIME_CONTAINER", value = "runtime" },
              { name = "EDA_COSMOS_FABRIC_AUTH_CONTAINER", value = "fabricAuth" },
              { name = "EDA_BLOB_ACCOUNT_URL", value = var.blob_account_url },
              { name = "EDA_BLOB_SESSIONS_CONTAINER", value = "sessions" },
              { name = "EDA_REDIS_URL", value = var.redis_url },
              { name = "EDA_FOUNDRY_PROJECT_ENDPOINT", value = var.foundry_project_endpoint },
              { name = "EDA_FOUNDRY_MODEL_DEPLOYMENT", value = var.foundry_model_deployment },
              { name = "EDA_MODEL_PROFILE", value = var.model_profile },
              { name = "EDA_FOUNDRY_HOSTING", value = "azure" },
              { name = "FABRIC_ENABLED", value = "true" },
              { name = "FABRIC_PROVIDER", value = var.fabric_provider },
              { name = "FABRIC_TENANT_ID", value = var.fabric_tenant_id },
              { name = "FABRIC_CLIENT_ID", value = var.fabric_client_id },
              { name = "FABRIC_KEY_VAULT_URL", value = var.fabric_key_vault_url },
              { name = "FABRIC_SIGNING_CERTIFICATE_NAME", value = var.fabric_signing_certificate_name },
              { name = "FABRIC_CACHE_WRAP_KEY_NAME", value = var.fabric_cache_wrap_key_name },
              ], var.fabric_provider == "semantic_model" ? [
              { name = "FABRIC_SEMANTIC_MODELS_JSON", value = var.fabric_semantic_models_json },
              ] : [
              { name = "FABRIC_ONTOLOGIES_JSON", value = var.fabric_ontologies_json },
            ])
          }
        ]
      }
    }
  }
}

output "environment_id" {
  value = azapi_resource.environment.id
}

output "api_id" {
  value = azapi_resource.api.id
}

output "api_url" {
  value = "https://${azapi_resource.api.output.properties.configuration.ingress.fqdn}"
}

output "cleanup_job_id" {
  value = var.deploy_image_dependent_resources ? azapi_resource.cleanup[0].id : null
}

output "fabric_acceptance_job_id" {
  value = var.deploy_image_dependent_resources && var.fabric_enabled ? azapi_resource.fabric_acceptance[0].id : null
}