from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import cast

from pydantic_settings import BaseSettings, SettingsConfigDict

WEB_SKILL_NAME = "web-artifacts-builder"
_REQUIRED_PATHS = (
    "SKILL.md",
    "LICENSE.txt",
    ".acquired.json",
    "scripts/init-artifact.sh",
    "scripts/bundle-artifact.sh",
    "scripts/shadcn-components.tar.gz",
)
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_COMMIT = re.compile(r"^[a-f0-9]{40}$")


class WebArtifactReadiness(StrEnum):
    READY = "ready"
    FAILED = "failed"


class WebArtifactSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WEB_ARTIFACTS_", env_file=".env", extra="forbid")

    skill_root: Path = Path("/opt/web-skills")


@dataclass(frozen=True)
class WebArtifactState:
    status: WebArtifactReadiness
    skill_path: Path | None = None


def web_artifact_readiness(settings: WebArtifactSettings) -> WebArtifactState:
    skill_path = settings.skill_root / WEB_SKILL_NAME
    if not skill_path.is_dir() or skill_path.is_symlink():
        return WebArtifactState(status=WebArtifactReadiness.FAILED)
    for relative in _REQUIRED_PATHS:
        candidate = skill_path / relative
        if not candidate.is_file() or candidate.is_symlink():
            return WebArtifactState(status=WebArtifactReadiness.FAILED)
    try:
        raw_metadata: object = json.loads((skill_path / ".acquired.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return WebArtifactState(status=WebArtifactReadiness.FAILED)
    if not isinstance(raw_metadata, dict):
        return WebArtifactState(status=WebArtifactReadiness.FAILED)
    metadata = cast(dict[str, object], raw_metadata)
    if not _COMMIT.fullmatch(str(metadata.get("commit", ""))):
        return WebArtifactState(status=WebArtifactReadiness.FAILED)
    for name in ("licenseSha256", "skillMdSha256"):
        if not _SHA256.fullmatch(str(metadata.get(name, ""))):
            return WebArtifactState(status=WebArtifactReadiness.FAILED)
    return WebArtifactState(status=WebArtifactReadiness.READY, skill_path=skill_path)


def require_web_artifact_skill(settings: WebArtifactSettings) -> Path:
    state = web_artifact_readiness(settings)
    if state.status is not WebArtifactReadiness.READY or state.skill_path is None:
        raise RuntimeError("mandatory Web Artifact Pack is incomplete")
    return state.skill_path
