from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

DOCUMENT_SKILL_NAMES = frozenset({"docx", "pdf", "pptx", "xlsx"})
WEB_SKILL_NAMES = frozenset({"web-artifacts-builder"})
ALL_SKILL_NAMES = DOCUMENT_SKILL_NAMES | WEB_SKILL_NAMES
type SkillPack = Literal["all", "documents", "web"]


class TermsNotAcceptedError(ValueError):
    pass


class SkillLockEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    name: str = Field(pattern=r"^(docx|pdf|pptx|xlsx|web-artifacts-builder)$")
    path: str = Field(pattern=r"^skills/(docx|pdf|pptx|xlsx|web-artifacts-builder)$")
    terms_url: str = Field(alias="termsUrl", pattern=r"^https://")
    license_url: str = Field(alias="licenseUrl", pattern=r"^https://")
    allowed_paths: tuple[str, ...] = Field(alias="allowedPaths")
    skill_md_sha256: str = Field(alias="skillMdSha256", pattern=r"^[a-f0-9]{64}$")
    license_sha256: str = Field(alias="licenseSha256", pattern=r"^[a-f0-9]{64}$")


class SkillsLock(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    schema_version: str = Field(alias="schemaVersion", pattern=r"^1\.0$")
    source: str = Field(pattern=r"^https://")
    commit: str = Field(pattern=r"^[a-f0-9]{40}$")
    terms_url: str = Field(alias="termsUrl", pattern=r"^https://")
    license_url: str = Field(alias="licenseUrl", pattern=r"^https://")
    archive_sha256: str = Field(alias="archiveSha256", pattern=r"^[a-f0-9]{64}$")
    skills: tuple[SkillLockEntry, ...]


def load_lock(path: Path) -> SkillsLock:
    lock = SkillsLock.model_validate_json(path.read_text(encoding="utf-8"))
    if {entry.name for entry in lock.skills} != set(ALL_SKILL_NAMES):
        raise ValueError("skill lock must contain every document and web artifact skill exactly once")
    return lock


def lock_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require_terms_acceptance(path: Path) -> SkillsLock:
    lock = load_lock(path)
    expected_hash = lock_sha256(path)
    if os.environ.get("EDA_DOCUMENT_TERMS_ACCEPTED") != expected_hash:
        raise TermsNotAcceptedError(
            "document skill terms at "
            f"{lock.terms_url} must be accepted with EDA_DOCUMENT_TERMS_ACCEPTED={expected_hash}"
        )
    return lock


def load_skill_pack(path: Path, pack: SkillPack) -> tuple[SkillsLock, tuple[SkillLockEntry, ...]]:
    lock = load_lock(path) if pack == "web" else require_terms_acceptance(path)
    selected_names = {
        "all": ALL_SKILL_NAMES,
        "documents": DOCUMENT_SKILL_NAMES,
        "web": WEB_SKILL_NAMES,
    }[pack]
    return lock, tuple(entry for entry in lock.skills if entry.name in selected_names)


def lock_as_canonical_json(path: Path) -> str:
    return json.dumps(load_lock(path).model_dump(mode="json", by_alias=True), sort_keys=True, separators=(",", ":"))
