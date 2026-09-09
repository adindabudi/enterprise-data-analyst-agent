from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Self
from uuid import UUID

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from eda_worker.fabric.ontology.config import OntologyTarget
from eda_worker.fabric.provider import FabricProvider

_ALIAS_PATTERN = re.compile(r"^[a-z][a-z0-9-]{1,39}$")
FABRIC_IQ_MCP_URL = "https://api.fabric.microsoft.com/v1/mcp/fabricaihub/integrations/m365"
FABRIC_IQ_VARIANT = "Fabric.Routing.PowerBIDataExploration"


class SemanticModelTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    model_id: UUID = Field(alias="modelId")
    description: str = Field(min_length=1, max_length=240, pattern=r"^[^\r\n\x00-\x1f<>]+$")
    routing_terms: tuple[str, ...] = Field(default=(), alias="routingTerms", max_length=64)


class FabricSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FABRIC_",
        env_file=".env",
        env_ignore_empty=True,
        extra="forbid",
        populate_by_name=True,
    )

    enabled: bool = False
    provider: FabricProvider | None = None
    tenant_id: UUID | None = None
    client_id: UUID | None = None
    key_vault_url: AnyHttpUrl | None = None
    signing_certificate_name: str | None = Field(default=None, min_length=1, max_length=128)
    cache_wrap_key_name: str | None = Field(default=None, min_length=1, max_length=128)
    models: dict[str, SemanticModelTarget] = Field(
        default_factory=dict,
        max_length=20,
        validation_alias="FABRIC_SEMANTIC_MODELS_JSON",
    )
    ontologies: dict[str, OntologyTarget] = Field(
        default_factory=dict,
        max_length=20,
        validation_alias="FABRIC_ONTOLOGIES_JSON",
    )
    timeout_seconds: int = Field(default=90, ge=10, le=120)
    max_analyst_turns: int = Field(default=6, ge=3, le=8)

    @model_validator(mode="after")
    def validate_profile(self) -> Self:
        self._validate_catalog_aliases(self.models)
        self._validate_catalog_aliases(self.ontologies)
        self._validate_unique_ontology_targets()

        if not self.enabled:
            if self.provider is not None or self.models or self.ontologies:
                raise ValueError("disabled Fabric configuration must not specify a provider or target catalog")
            return self

        if self.provider is None:
            raise ValueError("enabled Fabric configuration requires a selected provider")
        if None in {
            self.tenant_id,
            self.client_id,
            self.key_vault_url,
            self.signing_certificate_name,
            self.cache_wrap_key_name,
        }:
            raise ValueError("enabled Fabric configuration requires all auth settings")

        if self.provider is FabricProvider.SEMANTIC_MODEL:
            if not self.models or self.ontologies:
                raise ValueError("selected provider requires exactly its target catalog")
        elif not self.ontologies or self.models:
            raise ValueError("selected provider requires exactly its target catalog")
        return self

    @staticmethod
    def _validate_catalog_aliases(catalog: Mapping[str, object]) -> None:
        for alias in catalog:
            if not _ALIAS_PATTERN.fullmatch(alias):
                raise ValueError("target catalog alias must match ^[a-z][a-z0-9-]{1,39}$")

    def _validate_unique_ontology_targets(self) -> None:
        target_pairs = [(target.workspace_id, target.ontology_id) for target in self.ontologies.values()]
        if len(set(target_pairs)) != len(target_pairs):
            raise ValueError("ontology target UUID pairs must not be duplicate")
