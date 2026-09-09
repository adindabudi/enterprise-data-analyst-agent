from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from eda_worker.fabric.ontology.config import ONTOLOGY_ENDPOINT_TEMPLATE
from eda_worker.fabric.ontology.grounding import build_source_guide, normalize_ontology_schema
from eda_worker.fabric.ontology.mcp_client import EXPECTED_ONTOLOGY_TOOLS, EXPECTED_TOOL_DESCRIPTIONS
from eda_worker.fabric.ontology.readiness import build_tool_manifest

SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
UUID_PATTERN = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
URL_PATTERN = re.compile(r"https?://", re.IGNORECASE)
CREDENTIAL_TEXT_PATTERN = re.compile(
    r"\b(?:token|bearer|authorization|secret|password|api[-_]?key|private[-_]?key)\b", re.IGNORECASE
)


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def read_json_object(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"unable to read JSON artifact: {path}") from error
    return json_object(value, "artifact")


def write_json_atomically(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_text(canonical_json(value) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def validate_auth_contract(value: Mapping[str, object]) -> dict[str, object]:
    required = {
        "schemaVersion",
        "provider",
        "outcome",
        "proofKind",
        "referenceDiagnostic",
        "tenantTopology",
        "deploymentSha256",
        "endpointTemplateSha256",
        "targetCatalogSha256",
        "scopeSha256",
        "audienceSha256",
        "toolNames",
        "toolContractSha256",
        "runSha256",
    }
    require_exact_keys(value, required, "auth contract")
    if value["schemaVersion"] != 1 or value["provider"] != "ontology":
        raise ValueError("auth contract is not an ontology proof")
    if value["outcome"] != "success" or value["proofKind"] != "production_bff":
        raise ValueError("auth contract is not a successful production BFF proof")
    if value["referenceDiagnostic"] is not False or value["tenantTopology"] != "cross_tenant":
        raise ValueError("auth contract is diagnostic-only or same-tenant")
    for name in (
        "deploymentSha256",
        "endpointTemplateSha256",
        "targetCatalogSha256",
        "scopeSha256",
        "audienceSha256",
        "toolContractSha256",
        "runSha256",
    ):
        require_sha256(value[name], name)
    if value["endpointTemplateSha256"] != hashlib.sha256(ONTOLOGY_ENDPOINT_TEMPLATE.encode()).hexdigest():
        raise ValueError("auth contract endpoint template hash does not match")
    expected_tools = tuple(sorted(EXPECTED_ONTOLOGY_TOOLS))
    tool_names = string_sequence(value["toolNames"], "auth contract tool names")
    if tuple(tool_names) != expected_tools:
        raise ValueError("auth contract tool names do not match the accepted two-tool contract")
    manifest = build_tool_manifest(
        [
            {
                "name": name,
                "description": EXPECTED_TOOL_DESCRIPTIONS[name],
                "inputSchema": EXPECTED_ONTOLOGY_TOOLS[name],
            }
            for name in expected_tools
        ]
    )
    if value["toolContractSha256"] != manifest.contract_digest:
        raise ValueError("auth contract tool hash does not match the accepted two-tool contract")
    return dict(value)


def validate_catalog(value: Mapping[str, object]) -> list[dict[str, object]]:
    require_exact_keys(value, {"aliases"}, "target catalog")
    aliases = object_list(value["aliases"], "target catalog aliases")
    if not aliases or len(aliases) > 20:
        raise ValueError("target catalog must contain 1-20 aliases")
    normalized: list[dict[str, object]] = []
    names: set[str] = set()
    for item in aliases:
        alias = json_object(item, "target catalog alias")
        require_exact_keys(alias, {"alias", "description", "routingTerms", "targetPairSha256"}, "target catalog alias")
        alias_name = string_value(alias["alias"], "target catalog alias")
        if not re.fullmatch(r"[a-z][a-z0-9-]{1,39}", alias_name) or alias_name in names:
            raise ValueError("target catalog alias is invalid or duplicated")
        description = string_value(alias["description"], "target catalog description")
        routing_terms = string_sequence(alias["routingTerms"], "target catalog routing terms")
        if not routing_terms or len(routing_terms) > 64 or len(set(routing_terms)) != len(routing_terms):
            raise ValueError("target catalog routing terms are invalid")
        require_sha256(alias["targetPairSha256"], "target catalog target pair hash")
        names.add(alias_name)
        normalized.append(
            {
                "alias": alias_name,
                "description": description,
                "routingTerms": routing_terms,
                "targetPairSha256": alias["targetPairSha256"],
            }
        )
    return sorted(normalized, key=lambda item: cast(str, item["alias"]))


def build_provider_contract(
    *,
    auth_contract: Mapping[str, object],
    catalog: Mapping[str, object],
    inspection: Mapping[str, object],
) -> dict[str, object]:
    auth = validate_auth_contract(auth_contract)
    aliases = validate_catalog(catalog)
    if auth["targetCatalogSha256"] != sha256(catalog):
        raise ValueError("auth contract target catalog hash does not match")
    require_exact_keys(inspection, {"aliases"}, "inspection artifact")
    inspection_aliases = json_object(inspection["aliases"], "inspection aliases")
    catalog_names = {cast(str, item["alias"]) for item in aliases}
    if set(inspection_aliases) != catalog_names:
        raise ValueError("inspection aliases do not exactly match the target catalog")

    normalized_aliases: list[dict[str, object]] = []
    for item in aliases:
        alias = cast(str, item["alias"])
        raw_schema = json_object(inspection_aliases[alias], f"inspection alias {alias}")
        grounding = normalize_ontology_schema(raw_schema)
        source_guide = build_source_guide(
            alias=alias,
            description=cast(str, item["description"]),
            normalized_schema=grounding,
            routing_terms=tuple(cast(list[str], item["routingTerms"])),
        )
        normalized_aliases.append(
            {
                "alias": alias,
                "targetPairSha256": item["targetPairSha256"],
                "groundingSha256": sha256(grounding),
                "grounding": grounding,
                "sourceGuideSha256": sha256(source_guide),
                "sourceGuide": source_guide,
            }
        )
    contract: dict[str, object] = {
        "schemaVersion": 1,
        "provider": "ontology",
        "authContractSha256": sha256(auth_contract),
        "deploymentSha256": auth["deploymentSha256"],
        "endpointTemplateSha256": auth["endpointTemplateSha256"],
        "targetCatalogSha256": auth["targetCatalogSha256"],
        "scopeSha256": auth["scopeSha256"],
        "audienceSha256": auth["audienceSha256"],
        "toolContractSha256": auth["toolContractSha256"],
        "toolNames": auth["toolNames"],
        "aliases": normalized_aliases,
    }
    ensure_public_contract(contract)
    return contract


def validate_provider_contract(value: Mapping[str, object], auth_contract: Mapping[str, object]) -> dict[str, object]:
    auth = validate_auth_contract(auth_contract)
    required = {
        "schemaVersion",
        "provider",
        "authContractSha256",
        "deploymentSha256",
        "endpointTemplateSha256",
        "targetCatalogSha256",
        "scopeSha256",
        "audienceSha256",
        "toolContractSha256",
        "toolNames",
        "aliases",
    }
    require_exact_keys(value, required, "provider contract")
    if value["schemaVersion"] != 1 or value["provider"] != "ontology":
        raise ValueError("provider contract is not an ontology contract")
    if value["authContractSha256"] != sha256(auth_contract):
        raise ValueError("provider contract auth proof does not match")
    for name in (
        "deploymentSha256",
        "endpointTemplateSha256",
        "targetCatalogSha256",
        "scopeSha256",
        "audienceSha256",
        "toolContractSha256",
        "toolNames",
    ):
        if value[name] != auth[name]:
            raise ValueError(f"provider contract {name} does not match auth proof")
    aliases = object_list(value["aliases"], "provider contract aliases")
    if not aliases:
        raise ValueError("provider contract must contain aliases")
    ensure_public_contract(value)
    return dict(value)


def build_publication_payload(
    *,
    auth_contract: Mapping[str, object],
    provider_contract: Mapping[str, object],
    state: str,
) -> dict[str, object]:
    if state != "configured":
        raise ValueError("this script cannot promote ontology readiness")
    provider = validate_provider_contract(provider_contract, auth_contract)
    provider_digest = sha256(provider_contract)
    payload: dict[str, object] = {
        "id": f"feature-contract:fabric-ontology:{provider_digest}",
        "immutable": True,
        "provider": "ontology",
        "state": "configured",
        "providerContractSha256": provider_digest,
        "authContractSha256": provider["authContractSha256"],
        "deploymentSha256": provider["deploymentSha256"],
        "endpointTemplateSha256": provider["endpointTemplateSha256"],
        "targetCatalogSha256": provider["targetCatalogSha256"],
        "scopeSha256": provider["scopeSha256"],
        "audienceSha256": provider["audienceSha256"],
        "toolContractSha256": provider["toolContractSha256"],
    }
    ensure_public_contract(payload)
    return payload


def ensure_public_contract(value: Mapping[str, object]) -> None:
    forbidden_keys = {
        "endpoint",
        "workspaceid",
        "ontologyid",
        "tenantid",
        "targetid",
        "token",
        "authorization",
        "sourcemapping",
        "source_table",
        "clusteruri",
        "database",
        "raw",
        "topology",
    }

    def visit(item: object) -> None:
        if isinstance(item, Mapping):
            mapping = cast(Mapping[object, object], item)
            for key, nested in mapping.items():
                if not isinstance(key, str) or key.casefold() in forbidden_keys:
                    raise ValueError("public contract contains a forbidden field")
                visit(nested)
        elif isinstance(item, list):
            values = cast(list[object], item)
            for nested in values:
                visit(nested)
        elif isinstance(item, str) and (
            UUID_PATTERN.search(item) or URL_PATTERN.search(item) or CREDENTIAL_TEXT_PATTERN.search(item)
        ):
            raise ValueError("public contract contains an identifier, endpoint, or credential-shaped text")

    visit(value)


def require_exact_keys(value: Mapping[str, object], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise ValueError(f"{label} has missing or unknown fields")


def require_sha256(value: object, label: str) -> None:
    if not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value):
        raise ValueError(f"{label} must be a SHA-256 hash")


def string_value(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a nonempty string")
    return value


def string_sequence(value: object, label: str) -> list[str]:
    values = object_list(value, label)
    if not all(isinstance(item, str) and item for item in values):
        raise ValueError(f"{label} must contain nonempty strings")
    return [cast(str, item) for item in values]


def object_list(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")
    return [*cast(list[object], value)]


def json_object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    result: dict[str, object] = {}
    for key, item in cast(Mapping[object, object], value).items():
        if not isinstance(key, str):
            raise ValueError(f"{label} has a non-string key")
        result[key] = item
    return result
