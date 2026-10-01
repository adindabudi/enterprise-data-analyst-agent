variable "environment_name" {
  type = string
}

variable "location" {
  type = string
}

variable "resource_group_name" {
  type = string
}

variable "profile" {
  type = string
  validation {
    condition     = contains(["demo", "production"], var.profile)
    error_message = "profile must be demo or production."
  }
}

variable "principal_id" {
  type = string
}

variable "entra_client_id" {
  type = string
}

variable "entra_tenant_id" {
  type    = string
  default = ""
}

variable "model_profile" {
  type    = string
  default = "gpt-5.6-terra-medium-v1"

  validation {
    condition     = var.model_profile == "gpt-5.6-terra-medium-v1"
    error_message = "Only the Terra product profile gpt-5.6-terra-medium-v1 is supported."
  }
}

variable "model_capacity" {
  type    = number
  default = 25
}

variable "monthly_budget_amount" {
  type = number
}

variable "monitoring_alerts_enabled" {
  type    = bool
  default = false
}

variable "budget_action_group_name" {
  type    = string
  default = ""
}

variable "budget_alert_email" {
  type    = string
  default = ""
}

variable "api_min_replicas" {
  type    = number
  default = 1

  validation {
    condition     = var.api_min_replicas >= 1
    error_message = "api_min_replicas must be at least 1."
  }
}

variable "api_max_replicas" {
  type    = number
  default = 1
}

variable "redis_sku" {
  type    = string
  default = "Balanced_B0"
}

variable "redis_high_availability_enabled" {
  type    = bool
  default = false
}

variable "cosmos_autoscale_max_throughput" {
  type    = number
  default = 4000
}

variable "cosmos_continuous_backup_enabled" {
  type    = bool
  default = false
}

variable "product_data_public_access_enabled" {
  type    = bool
  default = true
}

variable "log_retention_in_days" {
  type    = number
  default = 30
}

variable "zone_redundancy_enabled" {
  type    = bool
  default = false
}

variable "defender_scan_cap_gb" {
  type    = number
  default = 100
}

variable "api_image" {
  type    = string
  default = "example.azurecr.io/eda-api@sha256:0000000000000000000000000000000000000000000000000000000000000000"
}

variable "worker_image" {
  type    = string
  default = "example.azurecr.io/eda-worker@sha256:0000000000000000000000000000000000000000000000000000000000000000"
}

variable "sandbox_image" {
  type     = string
  default  = null
  nullable = true
}

variable "deploy_image_dependent_resources" {
  type    = bool
  default = false
}

variable "documents_enabled" {
  type    = bool
  default = false
}

variable "powerbi_project_enabled" {
  type    = bool
  default = false

  validation {
    condition     = !var.powerbi_project_enabled
    error_message = "Power BI Project Pack requires the retired DTS runtime and is unavailable in this release."
  }
}

variable "fabric_enabled" {
  type    = bool
  default = false
}

variable "fabric_provider" {
  type    = string
  default = ""

  validation {
    condition     = contains(["", "ontology"], var.fabric_provider)
    error_message = "fabric_provider must be empty or ontology."
  }
}

variable "fabric_tenant_id" {
  type    = string
  default = ""
}

variable "fabric_client_id" {
  type    = string
  default = ""
}

variable "fabric_ontologies_json" {
  type    = string
  default = "{}"
}

variable "fabric_signing_certificate_name" {
  type    = string
  default = "fabric-oauth-signing"
}

variable "fabric_cache_wrap_key_name" {
  type    = string
  default = "fabric-cache-wrap"
}

variable "acceptance_principal_id" {
  type    = string
  default = ""
}
