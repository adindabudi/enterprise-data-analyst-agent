import tomllib
from importlib.metadata import version
from pathlib import Path

from eda_api.config import Settings


def test_preview_runtime_versions_are_exact() -> None:
    assert version("agent-framework-core") == "1.16.0"
    assert version("azure-containerapps-sandbox") == "0.1.0b4"


def test_worker_does_not_package_sandbox_artifact_tooling() -> None:
    manifest = tomllib.loads(Path("services/worker/pyproject.toml").read_text(encoding="utf-8"))
    dockerfile = Path("services/worker/Dockerfile").read_text(encoding="utf-8")

    assert "eda-artifacts" not in manifest["project"]["dependencies"]
    assert "packages/artifacts" not in dockerfile


def test_runtime_settings_require_redis(settings_values: dict[str, str]) -> None:
    settings = Settings.model_validate(
        {
            **settings_values,
            "redis_url": "redis://127.0.0.1:6379/0",
        }
    )

    assert settings.redis_stream_ttl_seconds == 3600
