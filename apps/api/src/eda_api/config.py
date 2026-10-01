from __future__ import annotations

import re
from functools import cached_property
from pathlib import Path
from typing import Any, Literal, Self, cast
from uuid import UUID

from eda_fabric_auth import FabricProvider
from pydantic import AliasChoices, AnyHttpUrl, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from eda_api.fabric_ontology import OntologyTarget

DEFAULT_FRONTEND_DIST = Path(__file__).resolve().parents[2] / "static"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EDA_", env_file=".env", extra="ignore", populate_by_name=True)

    app_env: Literal["development", "test", "production"] = "development"
    public_origin: AnyHttpUrl
    entra_tenant_id: UUID
    entra_client_id: UUID
    managed_identity_client_id: UUID | None = None
    entra_client_secret: SecretStr | None = Field(default=None, repr=False)
    cosmos_endpoint: AnyHttpUrl
    cosmos_database: str = "enterprise-data-analyst"
    cosmos_workspace_container: str = "workspace"
    cosmos_auth_container: str = "auth"
    cosmos_runtime_container: str = "runtime"
    cosmos_fabric_auth_container: str = "fabricAuth"
    blob_account_url: AnyHttpUrl
    blob_quarantine_container: str = "quarantine"
    blob_sessions_container: str = "sessions"
    cookie_secure: bool = True
    auth_flow_ttl_seconds: int = Field(default=600, ge=300, le=900)
    auth_session_ttl_seconds: int = Field(default=28800, ge=900, le=86400)
    upload_limit_bytes: int = Field(default=52428800, ge=1048576, le=52428800)
    redis_url: str
    redis_stream_ttl_seconds: int = Field(default=3600, ge=600, le=86400)
    redis_stream_max_entries: int = Field(default=10000, ge=100, le=100000)
    degraded_stream_buffer_bytes: int = Field(default=262144, ge=65536, le=262144)
    foundry_project_endpoint: AnyHttpUrl
    foundry_model_deployment: str = Field(min_length=1, max_length=128)
    eda_model_profile: Literal["gpt-5.6-terra-medium-v1"] = Field(
        default="gpt-5.6-terra-medium-v1",
        validation_alias=AliasChoices("eda_model_profile", "EDA_MODEL_PROFILE"),
    )
    foundry_hosting: Literal["azure", "anthropic"] = "azure"
    deployment_id: str = Field(default="local", min_length=1, max_length=128)
    worker_image_digest: str | None = Field(default=None, pattern=r"^sha256:[a-f0-9]{64}$")
    sandbox_image_digest: str | None = Field(default=None, pattern=r"^sha256:[a-f0-9]{64}$")
    model_contract_verified: bool = False
    tokenizer_calibrated: bool = False
    analysis_runtime_enabled: bool = False
    analysis_max_active_per_replica: int = Field(default=2, ge=1, le=8)
    analysis_deployment_limit: int = Field(default=5, ge=1, le=64)
    analysis_queue_depth: int = Field(default=50, ge=1, le=500)
    analysis_per_owner_limit: int = Field(default=10, ge=1, le=100)
    analysis_task_budget_minutes: int = Field(default=60, ge=5, le=240)
    analysis_streaming_enabled: bool = True
    entra_federation_ready: bool = False
    core_ready: bool = False
    documents_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("DOCUMENTS_ENABLED", "EDA_DOCUMENTS_ENABLED"),
    )
    powerbi_project_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("POWERBI_PROJECT_ENABLED", "EDA_POWERBI_PROJECT_ENABLED"),
    )
    fabric_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("FABRIC_ENABLED", "EDA_FABRIC_ENABLED"),
    )
    fabric_provider: FabricProvider | None = Field(
        default=None,
        validation_alias=AliasChoices("FABRIC_PROVIDER", "EDA_FABRIC_PROVIDER"),
    )
    fabric_tenant_id: UUID | None = Field(
        default=None,
        validation_alias=AliasChoices("FABRIC_TENANT_ID", "EDA_FABRIC_TENANT_ID"),
    )
    fabric_client_id: UUID | None = Field(
        default=None,
        validation_alias=AliasChoices("FABRIC_CLIENT_ID", "EDA_FABRIC_CLIENT_ID"),
    )
    fabric_key_vault_url: AnyHttpUrl | None = Field(
        default=None,
        validation_alias=AliasChoices("FABRIC_KEY_VAULT_URL", "EDA_FABRIC_KEY_VAULT_URL"),
    )
    fabric_signing_certificate_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        validation_alias=AliasChoices("FABRIC_SIGNING_CERTIFICATE_NAME", "EDA_FABRIC_SIGNING_CERTIFICATE_NAME"),
    )
    fabric_cache_wrap_key_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        validation_alias=AliasChoices("FABRIC_CACHE_WRAP_KEY_NAME", "EDA_FABRIC_CACHE_WRAP_KEY_NAME"),
    )
    fabric_ontologies: dict[str, OntologyTarget] = Field(
        default_factory=dict,
        max_length=1,
        validation_alias=AliasChoices("fabric_ontologies", "FABRIC_ONTOLOGIES_JSON"),
    )
    frontend_dist: Path = DEFAULT_FRONTEND_DIST
    model_contract_path: str = "/app/config/model-contract.json"
    tokenizer_calibration_path: str = "/app/config/tokenizer-calibration.json"

    @model_validator(mode="after")
    def validate_identity_credential(self) -> Self:
        if self.app_env == "production":
            if self.managed_identity_client_id is None:
                raise ValueError("managed_identity_client_id is required when app_env is production")
            if self.entra_client_secret is not None:
                raise ValueError("production client secret is forbidden; use managed identity federation")
            if not self.redis_url.startswith("rediss://"):
                raise ValueError("production redis_url must use TLS")
        elif self.managed_identity_client_id is None and self.entra_client_secret is None:
            raise ValueError("managed_identity_client_id or entra_client_secret is required")
        return self

    @model_validator(mode="before")
    @classmethod
    def reject_fabric_target_catalogs(cls, values: Any) -> Any:
        forbidden_catalogs = {
            "powerbi_project_models",
            "POWERBI_PROJECT_MODELS_JSON",
        }
        if isinstance(values, dict) and forbidden_catalogs & values.keys():
            raise ValueError("API configuration must not receive provider target catalogs")
        return cast(Any, values)

    @model_validator(mode="after")
    def validate_fabric_profile(self) -> Self:
        if not self.fabric_enabled:
            if self.fabric_provider is not None or self.fabric_ontologies:
                raise ValueError("disabled Fabric configuration must not specify a provider or target catalog")
            return self
        if self.fabric_provider is None:
            raise ValueError("enabled Fabric configuration requires a selected provider")
        if None in {
            self.fabric_tenant_id,
            self.fabric_client_id,
            self.fabric_key_vault_url,
            self.fabric_signing_certificate_name,
            self.fabric_cache_wrap_key_name,
        }:
            raise ValueError("enabled Fabric configuration requires all auth settings")
        if any(not re.fullmatch(r"[a-z][a-z0-9-]{1,39}", alias) for alias in self.fabric_ontologies):
            raise ValueError("ontology target alias is invalid")
        return self

    @model_validator(mode="after")
    def validate_model_profile(self) -> Self:
        if self.foundry_hosting != "azure":
            raise ValueError("GPT-5.6 Terra requires Azure hosting")
        if self.powerbi_project_enabled:
            raise ValueError("Power BI Project Pack is unavailable in this release")
        if self.core_ready and (
            not self.model_contract_verified
            or not self.tokenizer_calibrated
            or not self.analysis_runtime_enabled
            or not self.entra_federation_ready
        ):
            raise ValueError("core_ready requires every execution and federation gate")
        if self.analysis_deployment_limit < self.analysis_max_active_per_replica:
            raise ValueError("analysis deployment limit must cover one replica's concurrency")
        return self

    @cached_property
    def authority(self) -> str:
        return f"https://login.microsoftonline.com/{self.entra_tenant_id}"

    @cached_property
    def redirect_uri(self) -> str:
        return f"{str(self.public_origin).rstrip('/')}/api/auth/callback"
