from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class DocumentSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DOCUMENTS_", env_file=".env", extra="forbid")

    enabled: bool = False
    skill_root: Path = Path("/opt/document-skills")
    acceptance_evidence_path: Path = Field(default=Path("/app/config/document-acceptance.json"))
