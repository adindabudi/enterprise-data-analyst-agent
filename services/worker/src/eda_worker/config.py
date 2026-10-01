from __future__ import annotations

from pathlib import Path
from typing import Literal, Self
from uuid import UUID

from pydantic import AliasChoices, AnyHttpUrl, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EDA_", env_file=".env", extra="ignore", populate_by_name=True)

    app_env: Literal["development", "test", "production"] = "development"
    managed_identity_client_id: UUID | None = None
    cosmos_endpoint: AnyHttpUrl
    cosmos_database: str = "enterprise-data-analyst"
    cosmos_workspace_container: str = "workspace"
    cosmos_runtime_container: str = "runtime"
    blob_account_url: AnyHttpUrl
    blob_sessions_container: str = "sessions"
    redis_url: str
    redis_stream_ttl_seconds: int = Field(default=3600, ge=600, le=86400)
    redis_stream_max_entries: int = Field(default=10000, ge=100, le=100000)
    foundry_project_endpoint: AnyHttpUrl = Field(
        validation_alias=AliasChoices("foundry_project_endpoint", "FOUNDRY_PROJECT_ENDPOINT")
    )
    foundry_model_deployment: str = Field(min_length=1, max_length=128)
    eda_model_profile: Literal["gpt-5.6-terra-medium-v1"] = Field(
        default="gpt-5.6-terra-medium-v1",
        validation_alias=AliasChoices("eda_model_profile", "EDA_MODEL_PROFILE"),
    )
    foundry_hosting: Literal["azure"] = "azure"
    deployment_id: str = Field(default="local", min_length=1, max_length=128)
    worker_image_digest: str | None = Field(default=None, pattern=r"^sha256:[a-f0-9]{64}$")
    sandbox_image_digest: str | None = Field(default=None, pattern=r"^sha256:[a-f0-9]{64}$")
    sandbox_subscription_id: UUID
    sandbox_resource_group: str = Field(min_length=1, max_length=90)
    sandbox_group: str = Field(min_length=1, max_length=63)
    sandbox_region: str = Field(min_length=1, max_length=64)
    sandbox_disk_image_id: str = Field(min_length=1, max_length=128)
    model_contract_path: Path = Path("/app/config/model-contract.json")
    tokenizer_calibration_path: Path = Path("/app/config/tokenizer-calibration.json")

    @model_validator(mode="after")
    def validate_transport_security(self) -> Self:
        if self.app_env == "production":
            if not self.redis_url.startswith("rediss://"):
                raise ValueError("production redis_url must use TLS")
        if self.foundry_hosting != "azure":
            raise ValueError("GPT-5.6 Terra requires Azure hosting")
        return self
