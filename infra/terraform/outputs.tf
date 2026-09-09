output "resource_group_name" {
  value = module.resource_group.name
}

output "deployment_id" {
  value = sha256(jsonencode({ env = var.environment_name, profile = var.profile, model = var.model_profile }))
}

output "api_url" {
  value = module.container_apps.api_url
}

output "profile" {
  value = var.profile
}

output "model_profile" {
  value = module.foundry.model_profile
}

output "model_deployment_name" {
  value = module.foundry.deployment_name
}

output "project_endpoint" {
  value = module.foundry.project_endpoint
}

output "container_registry_id" {
  value = module.acr.registry_id
}

output "container_registry_login_server" {
  value = module.acr.login_server
}

output "cosmos_account_id" {
  value = module.cosmos.account_id
}

output "cosmos_endpoint" {
  value = module.cosmos.endpoint
}

output "storage_account_id" {
  value = module.storage.account_id
}

output "storage_blob_endpoint" {
  value = module.storage.blob_endpoint
}

output "redis_cluster_id" {
  value = module.redis.cluster_id
}

output "redis_hostname" {
  value = module.redis.hostname
}

output "redis_port" {
  value = module.redis.port
}

output "web_identity_id" {
  value = module.identities.web_identity_id
}

output "web_identity_client_id" {
  value = module.identities.web_identity_client_id
}

output "web_identity_principal_id" {
  value = module.identities.web_identity_principal_id
}

output "worker_identity_id" {
  value = module.identities.worker_identity_id
}

output "worker_identity_client_id" {
  value = module.identities.worker_identity_client_id
}

output "worker_identity_principal_id" {
  value = module.identities.worker_identity_principal_id
}

output "session_init_identity_id" {
  value = module.identities.session_init_identity_id
}

output "session_init_identity_client_id" {
  value = module.identities.session_init_identity_client_id
}

output "session_init_identity_principal_id" {
  value = module.identities.session_init_identity_principal_id
}

output "virtual_network_id" {
  value = module.network.virtual_network_id
}

output "aca_subnet_id" {
  value = module.network.aca_subnet_id
}

output "sandbox_subnet_id" {
  value = module.network.sandbox_subnet_id
}

output "foundry_agent_subnet_id" {
  value = module.network.foundry_agent_subnet_id
}

output "private_endpoints_subnet_id" {
  value = module.network.private_endpoints_subnet_id
}

output "log_analytics_workspace_id" {
  value = module.monitoring.workspace_id
}

output "app_insights_id" {
  value = module.monitoring.app_insights_id
}

output "container_apps_environment_id" {
  value = module.container_apps.environment_id
}

output "api_app_id" {
  value = module.container_apps.api_id
}

output "cleanup_job_id" {
  value = module.container_apps.cleanup_job_id
}

output "fabric_acceptance_job_id" {
  value = var.fabric_enabled ? module.container_apps.fabric_acceptance_job_id : null
}

output "fabric_vault_url" {
  value = var.fabric_enabled ? module.fabric_auth[0].vault_uri : null
}

output "sandbox_group_id" {
  value = var.deploy_image_dependent_resources ? module.sandbox_group[0].sandbox_group_id : null
}

output "budget_action_group_id" {
  value = var.monitoring_alerts_enabled ? module.budgets_alerts[0].action_group_id : null
}
