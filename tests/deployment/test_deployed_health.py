from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, cast
from urllib.request import urlopen
from uuid import UUID

import pytest

ROOT = Path(__file__).resolve().parents[2]
REQUIRED_AZD_VALUES = (
    "AZURE_ENV_NAME",
    "AZURE_RESOURCE_GROUP",
    "API_APP_ID",
    "CLEANUP_JOB_ID",
    "CONTAINER_REGISTRY_LOGIN_SERVER",
    "AGENT_LONG_JOB_RESPONSES_ENDPOINT",
    "HOSTED_AGENT_PRINCIPAL_ID",
    "EDA_SANDBOX_GROUP",
    "PROFILE",
)


def test_deployment_tooling_requires_immutable_images_and_ordered_smoke_gates() -> None:
    pin_script = (ROOT / "scripts" / "pin-application-images.sh").read_text(encoding="utf-8")
    smoke_script = (ROOT / "scripts" / "deploy-smoke.sh").read_text(encoding="utf-8")
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

    for source in (pin_script, smoke_script):
        assert "set -eu" in source
        assert "fail()" in source
        assert "azd env get-values" in source
    for required_command in (
        "az acr manifest show-metadata",
        "az containerapp update",
        "az containerapp job update",
        "@sha256:",
        "images.json",
    ):
        assert required_command in pin_script
    for required_gate in (
        "/health/ready",
        "HOSTED_AGENT_PRINCIPAL_ID",
        "scripts/test-entra-app.py",
        "scripts/verify-rbac.py",
        "deployed-storage-gate",
        "model-contract",
        "sandbox-benchmark",
        "acceptance-core",
        "playwright",
        "test_telemetry_leaks.py",
    ):
        assert required_gate in smoke_script
    assert smoke_script.index("/health/ready") < smoke_script.index("scripts/test-entra-app.py")
    assert smoke_script.index("scripts/test-entra-app.py") < smoke_script.index("scripts/verify-rbac.py")
    assert smoke_script.index("scripts/verify-rbac.py") < smoke_script.index("deployed-storage-gate")
    assert smoke_script.index("acceptance-core") < smoke_script.index("playwright")
    assert smoke_script.index("playwright") < smoke_script.index("test_telemetry_leaks.py")
    assert "deployed-storage-gate:" in makefile
    assert "deployed-core-gate:" in makefile


def _cloud_environment() -> dict[str, str]:
    if not os.environ.get("EDA_APP_URL"):
        pytest.skip("set EDA_APP_URL to run deployed health checks")
    if shutil.which("az") is None:
        pytest.skip("Azure CLI is unavailable")
    account = subprocess.run(
        ["az", "account", "show", "--only-show-errors", "--output", "none"],  # noqa: S607 -- fixed Azure CLI prerequisite check.
        check=False,
        capture_output=True,
        encoding="utf-8",
    )
    if account.returncode != 0:
        pytest.skip("Azure credentials are unavailable")
    missing = [name for name in REQUIRED_AZD_VALUES if not os.environ.get(name)]
    if missing:
        pytest.fail(f"deployed health checks require azd values: {', '.join(missing)}")
    return {name: os.environ[name] for name in REQUIRED_AZD_VALUES}


def _az_json(*arguments: str) -> dict[str, Any]:
    result = subprocess.run(  # noqa: S603 -- callers provide fixed Azure CLI arguments.
        ["az", *arguments, "--only-show-errors", "--output", "json"],  # noqa: S607 -- Azure CLI is explicit.
        check=False,
        capture_output=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
    value: object = json.loads(result.stdout)
    assert isinstance(value, dict)
    return cast(dict[str, Any], value)


def _container_image(resource: dict[str, Any]) -> str:
    containers = cast(list[dict[str, Any]], resource["properties"]["template"]["containers"])
    assert isinstance(containers, list) and len(containers) == 1
    image: object = containers[0]["image"]
    assert isinstance(image, str)
    return image


@pytest.mark.cloud
def test_deployed_health_and_resource_contracts() -> None:
    environment = _cloud_environment()
    app_url = os.environ["EDA_APP_URL"].rstrip("/")
    with urlopen(f"{app_url}/health/ready", timeout=30) as response:  # noqa: S310 -- explicit deployed application URL.
        assert response.status == 200
        health: object = json.loads(response.read())
    assert isinstance(health, dict)
    health_text = json.dumps(health).lower()
    for dependency in ("cosmos", "blob", "redis", "foundry", "hostedagent", "sandbox", "auth"):
        assert dependency in health_text
    for expected_status in ("core", "ready", "fabric", "disabled", "documents", "powerbi"):
        assert expected_status in health_text

    image_manifest = ROOT / ".azure" / environment["AZURE_ENV_NAME"] / "images.json"
    assert image_manifest.is_file(), "image pinning manifest is missing"
    images: object = json.loads(image_manifest.read_text(encoding="utf-8"))
    assert isinstance(images, dict)
    image_values = cast(dict[str, dict[str, str]], images)

    api = _az_json("containerapp", "show", "--ids", environment["API_APP_ID"])
    cleanup = _az_json("containerapp", "job", "show", "--ids", environment["CLEANUP_JOB_ID"])
    sandbox_group = _az_json(
        "resource",
        "show",
        "--resource-group",
        environment["AZURE_RESOURCE_GROUP"],
        "--resource-type",
        "Microsoft.App/sandboxGroups",
        "--name",
        environment["EDA_SANDBOX_GROUP"],
    )
    api_image = _container_image(api)
    cleanup_image = _container_image(cleanup)
    assert api_image == image_values["api"]["image"]
    assert cleanup_image == image_values["worker"]["image"]
    assert all("@sha256:" in image for image in (api_image, cleanup_image))
    assert environment["AGENT_LONG_JOB_RESPONSES_ENDPOINT"].startswith("https://")
    assert environment["AGENT_LONG_JOB_RESPONSES_ENDPOINT"].endswith("/responses")
    UUID(environment["HOSTED_AGENT_PRINCIPAL_ID"])
    assert sandbox_group["id"].endswith(f"/sandboxGroups/{environment['EDA_SANDBOX_GROUP']}")

    ingress = api["properties"]["configuration"]["ingress"]
    assert ingress["external"] is True
    schedule = cleanup["properties"]["configuration"]["scheduleTriggerConfig"]
    assert schedule["cronExpression"] == "0 2 * * *"
