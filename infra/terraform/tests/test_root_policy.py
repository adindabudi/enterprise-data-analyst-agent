from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_versions_pin_exact_terraform_and_provider_releases() -> None:
    versions = (ROOT / "infra/terraform/versions.tf").read_text(encoding="utf-8")

    assert 'required_version = "= 1.15.8"' in versions
    assert 'source  = "hashicorp/azurerm"' in versions
    assert 'version = "= 4.81.0"' in versions
    assert 'source  = "azure/azapi"' in versions
    assert 'version = "= 2.11.0"' in versions
    assert 'source  = "hashicorp/azuread"' in versions
    assert 'version = "= 3.9.0"' in versions


def test_every_azapi_child_declares_the_azure_provider_source() -> None:
    modules = ROOT / "infra/terraform/modules"
    for main in modules.glob("*/main.tf"):
        if 'resource "azapi_' not in main.read_text(encoding="utf-8"):
            continue
        versions = main.with_name("versions.tf")
        assert versions.is_file(), f"missing AzAPI provider declaration for {main.parent.name}"
        assert 'source = "azure/azapi"' in versions.read_text(encoding="utf-8")


def test_root_variables_expose_only_the_terra_model_contract() -> None:
    variables = (ROOT / "infra/terraform/variables.tf").read_text(encoding="utf-8")

    assert 'variable "model_profile"' in variables
    assert 'default = "gpt-5.6-terra-medium-v1"' in variables
    assert "Only the Terra product profile gpt-5.6-terra-medium-v1 is supported." in variables
    assert 'variable "claude_model"' not in variables
    assert 'variable "claude_model_version"' not in variables
    assert 'variable "claude_hosting"' not in variables
    assert 'variable "fabric_provider"' in variables
    assert 'variable "fabric_ontologies_json"' in variables
    assert 'variable "sandbox_image"' in variables


def test_root_main_keeps_terraform_as_parity_and_preserves_foundation_mode() -> None:
    main = (ROOT / "infra/terraform/main.tf").read_text(encoding="utf-8")

    assert 'module "container_apps"' in main
    assert "deploy_image_dependent_resources = var.deploy_image_dependent_resources" in main
    assert 'module "sandbox_group"' in main
    assert 'module "session_pool"' not in main
    assert 'module "durable_task"' not in main
    assert "var.deploy_image_dependent_resources ? 1 : 0" in main
    assert 'module "fabric_auth"' in main
    assert 'module "budgets_alerts"' in main
    assert "./modules/identities" in main
    assert "./modules/foundry" in main
    assert 'module "resource_group"' in main
    assert 'source   = "./modules/resource_group"' in main
    assert "durable_task_endpoint" not in main
    assert "durable_task_hub_name" not in main
    sandbox_module = main.split('module "sandbox_group"', maxsplit=1)[1].split('module "budgets_alerts"', maxsplit=1)[0]
    assert "worker_identity_principal_id" not in sandbox_module
    assert "provisioning_principal_id" in sandbox_module
    assert "= var.principal_id" in sandbox_module
    assert "web_identity_principal_id" in sandbox_module
    assert "= module.identities.web_identity_principal_id" in sandbox_module

    sandbox_source = (ROOT / "infra/terraform/modules/sandbox_group/main.tf").read_text(encoding="utf-8")
    assert 'resource "azurerm_role_assignment" "provisioning_sandbox_data_owner"' in sandbox_source
    assert "principal_id       = var.provisioning_principal_id" in sandbox_source
    assert 'resource "azurerm_role_assignment" "web_sandbox_data_owner"' in sandbox_source
    assert "principal_id       = var.web_identity_principal_id" in sandbox_source
    assert "c24cf47c-5077-412d-a19c-45202126392c" in sandbox_source


def test_outputs_keep_azd_bridge_values_and_optional_job_gates() -> None:
    outputs = (ROOT / "infra/terraform/outputs.tf").read_text(encoding="utf-8")

    for name in (
        'output "container_registry_login_server"',
        'output "api_url"',
        'output "api_app_id"',
        'output "cleanup_job_id"',
        'output "sandbox_group_id"',
    ):
        assert name in outputs
