from __future__ import annotations

import re
from enum import StrEnum
from typing import Literal
from uuid import UUID

from eda_contracts import ArtifactRef
from pydantic import BaseModel, ConfigDict, Field

_UUID_PATTERN = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
_URL_PATTERN = re.compile(r"https?://", re.I)
_FORBIDDEN_GUIDE_PATTERN = re.compile(
    r"\b(?:token|headers?|tenant|credential|secret|workspace|endpoint|ontology_id|item_id|model_id)\b"
    r"|execute_dax_query|search_ontology|list_ontology_entity_types|naturalLanguageResponse",
    re.I,
)


class FabricModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FabricQueryPurpose(StrEnum):
    SCHEMA = "schema"
    AGGREGATE = "aggregate"
    CONTROL_TOTAL = "control_total"


class FabricQueryOperation(FabricModel):
    semantic_model: str = Field(pattern=r"^[a-z][a-z0-9-]{1,39}$")
    purpose: FabricQueryPurpose
    question: str = Field(min_length=1, max_length=4_000)


class FabricQueryResult(FabricModel):
    status: Literal["ok", "correctable_error", "authorization_error", "cancelled"]
    summary: str = Field(max_length=8_000)
    query_ref: str | None = Field(default=None, pattern=r"^fabric-query-[A-Za-z0-9_-]+$")
    artifact_refs: tuple[ArtifactRef, ...] = Field(default=(), max_length=4)
    error_code: str | None = Field(default=None, max_length=80)
    retryable: bool = False


class FabricSourceGuide(FabricModel):
    alias: str = Field(pattern=r"^[a-z][a-z0-9-]{1,39}$")
    description: str = Field(min_length=1, max_length=240)
    vocabulary: tuple[str, ...] = Field(default=(), max_length=256)


class FabricPrincipal(FabricModel):
    tenant_id: UUID
    owner_object_id: UUID
    audience: UUID


def build_query_fabric_description(guides: tuple[FabricSourceGuide, ...]) -> str:
    if not guides:
        raise ValueError("Fabric source guides are required")
    ordered = tuple(sorted(guides, key=lambda item: item.alias))
    aliases = [guide.alias for guide in ordered]
    if len(set(aliases)) != len(aliases):
        raise ValueError("Fabric source aliases must be unique")
    source_lines: list[str] = []
    for guide in ordered:
        vocabulary = tuple(sorted(set(guide.vocabulary), key=str.casefold))
        guide_text = "\n".join((guide.alias, guide.description, *vocabulary))
        if (
            _UUID_PATTERN.search(guide_text)
            or _URL_PATTERN.search(guide_text)
            or _FORBIDDEN_GUIDE_PATTERN.search(guide_text)
        ):
            raise ValueError("Fabric source guide contains provider topology or credential data")
        concepts = f" Available concepts: {', '.join(vocabulary)}." if vocabulary else ""
        source_lines.append(f"- {guide.alias}: {guide.description}{concepts}")
    sources = "\n".join(source_lines)
    description = "\n".join(
        (
            "Query one configured Microsoft Fabric source for authoritative schema, entities, properties, "
            "relationships, metrics, aggregates, control totals, or time-series values.",
            "Available sources:",
            sources,
            "Choose exactly one matching alias. If more than one source could answer, ask the user to choose "
            "before calling this tool. Do not call this tool when no source matches.",
            "Write the question in English and self-contained, naming the source's own properties and stored "
            "values; translate the user's wording rather than passing it through, because the source matches "
            "literal schema terms and resolves no synonyms.",
            "Ask a time-series comparison as 'show any <entity> that ever had <Property> lower/higher than "
            "<value>', which returns the matching aggregate per entity; the source often ignores the threshold "
            "itself, so re-check every returned row against it and never report an unfiltered row as a match.",
            "Example success: for a sales total that matches only `sales`, call this tool with source alias "
            "`sales`, purpose `aggregate`, and a bounded question naming that source's own properties.",
            'Example ambiguity: if both `sales` and `finance` could answer "quarterly revenue", ask which '
            "source to use; do not call both.",
            "Example failure: when the result is `authorization_error`, report that status and do not supply "
            "a value from model knowledge.",
        )
    )
    if len(description.encode("utf-8")) > 16_384:
        raise ValueError("Fabric source guide exceeds 16 KiB")
    return description
