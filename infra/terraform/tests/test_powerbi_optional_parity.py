from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_bicep_and_terraform_default_powerbi_project_to_disabled() -> None:
    bicep = read("infra/bicep/main.bicep")
    terraform_variables = read("infra/terraform/variables.tf")
    demo = read("infra/terraform/parameters/demo.tfvars")
    production = read("infra/terraform/parameters/production.tfvars")

    assert "param powerBiProjectEnabled bool = false" in bicep
    assert "powerBiProjectModelsJson" not in bicep
    assert 'variable "powerbi_project_enabled"' in terraform_variables
    assert 'variable "powerbi_project_models_json"' not in terraform_variables
    assert "powerbi_project_enabled            = false" in demo
    assert "powerbi_project_enabled            = false" in production
    assert "powerbi_project_models_json" not in demo
    assert "powerbi_project_models_json" not in production


def test_the_dts_bound_powerbi_pack_is_rejected() -> None:
    bicep = read("infra/bicep/main.bicep")
    terraform_variables = read("infra/terraform/variables.tf")

    message = "Power BI Project Pack requires the retired DTS runtime"
    assert message in bicep
    assert "powerBiProjectEnabled: powerBiProjectGuard" in bicep
    assert "condition     = !var.powerbi_project_enabled" in terraform_variables
    assert message in terraform_variables


def test_disabled_powerbi_pack_has_no_runtime_catalog_surface() -> None:
    bicep_apps = read("infra/bicep/modules/container-apps.bicep")
    terraform_apps = read("infra/terraform/modules/container_apps/main.tf")

    for source in (bicep_apps, terraform_apps):
        assert "POWERBI_PROJECT_ENABLED" in source
        assert "POWERBI_PROJECT_MODELS_JSON" not in source


def test_terraform_bridge_overrides_checked_in_demo_identity_and_powerbi_intent() -> None:
    bridge = read("scripts/deploy-terraform-environment.sh")

    assert "powerbi_project_enabled: $powerBiProjectEnabled" in bridge
    assert "powerbi_project_models_json" not in bridge
    assert "POWERBI_PROJECT_MODELS_JSON" not in bridge
    assert 'POWERBI_PROJECT_ENABLED="${POWERBI_PROJECT_ENABLED:-false}"' in bridge
    assert "resource_group_name: $resourceGroupName" in bridge
    assert "principal_id: $principalId" in bridge
