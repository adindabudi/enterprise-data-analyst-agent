terraform {
  required_version = "= 1.15.8"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "= 4.81.0"
    }
    azapi = {
      source  = "azure/azapi"
      version = "= 2.11.0"
    }
    azuread = {
      source  = "hashicorp/azuread"
      version = "= 3.9.0"
    }
  }
}
