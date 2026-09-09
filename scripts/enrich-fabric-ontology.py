"""Write semantic enrichment into a Fabric IQ ontology through the Fabric REST API.

Semantic enrichment (descriptions, synonyms, and custom key-value attributes) is
documented as a UI gesture only, and it is absent from both the published
`entityType/1.0.0` JSON schema and the REST definition reference. It is
nevertheless accepted and persisted by `updateDefinition` under a
`semanticEnrichment` object on entity types, on individual properties, and on
relationship types. This script writes it from a checked-in file so the ontology
vocabulary is reproducible instead of hand-typed into the portal.

`updateDefinition` replaces the whole item definition, so the current definition
is read first, enriched in memory, and written back whole. The untouched
definition is always saved to disk first.

Dry run by default; pass --apply to write.
"""

from __future__ import annotations

import argparse
import base64
import json
import shutil
import subprocess
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import httpx
from eda_api.fabric_auth.ontology import MAX_ONTOLOGY_SCHEMA_CHARS, ontology_schema

FABRIC = "https://api.fabric.microsoft.com"
POLL_LIMIT = 60

# Only entity types accept synonyms; properties and relationship types do not.
# https://learn.microsoft.com/fabric/iq/ontology/how-to-add-semantic-enrichment
ENTITY_KEYS = frozenset({"description", "synonyms", "customAttributes", "properties", "timeseriesProperties"})
MEMBER_KEYS = frozenset({"description", "customAttributes"})


def token(resource: str) -> str:
    az = shutil.which("az")
    if az is None:
        raise RuntimeError("the Azure CLI is required to call the Fabric REST API")
    result = subprocess.run(  # noqa: S603 - fixed argument list, operator's own CLI session
        [az, "account", "get-access-token", "--resource", resource, "--query", "accessToken", "-o", "tsv"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def call(method: str, url: str, bearer: str, body: object | None = None) -> httpx.Response:
    return httpx.request(
        method,
        url,
        content=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {bearer}", "Content-Type": "application/json"},
        follow_redirects=False,
        timeout=180,
    )


def decoded(part: dict[str, str]) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(base64.b64decode(part["payload"])))


def encoded(part: dict[str, str], payload: dict[str, Any]) -> dict[str, str]:
    return {**part, "payload": base64.b64encode(json.dumps(payload).encode()).decode()}


def enrichment_of(spec: dict[str, Any], *, allow_synonyms: bool, where: str) -> dict[str, Any]:
    allowed = ENTITY_KEYS if allow_synonyms else MEMBER_KEYS
    unknown = set(spec) - allowed
    if unknown:
        raise ValueError(f"{where}: unsupported enrichment keys {sorted(unknown)}")
    enrichment: dict[str, Any] = {}
    description = spec.get("description")
    if description is not None:
        if not isinstance(description, str) or not description.strip():
            raise ValueError(f"{where}: description must be a non-empty string")
        enrichment["description"] = description
    synonyms = spec.get("synonyms")
    if synonyms is not None:
        terms = cast(list[Any], synonyms)
        if not isinstance(synonyms, list) or not all(isinstance(term, str) and term.strip() for term in terms):
            raise ValueError(f"{where}: synonyms must be a list of non-empty strings")
        enrichment["synonyms"] = terms
    attributes = spec.get("customAttributes")
    if attributes is not None:
        pairs = cast(dict[Any, Any], attributes)
        if not isinstance(attributes, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in pairs.items()
        ):
            raise ValueError(f"{where}: customAttributes must be a flat map of string to string")
        enrichment["customAttributes"] = pairs
    return enrichment


def carries_enrichment(value: object) -> bool:
    if not isinstance(value, Mapping):
        return False
    stored = cast(Mapping[str, object], value)
    return any(stored.get(key) for key in ("description", "synonyms", "customAttributes"))


def enrich_members(members: list[dict[str, Any]], specs: dict[str, Any], *, where: str) -> list[str]:
    by_name = {str(member["name"]): member for member in members}
    missing = set(specs) - set(by_name)
    if missing:
        raise ValueError(f"{where}: no such properties {sorted(missing)}")
    applied: list[str] = []
    for name, member in by_name.items():
        spec = cast(dict[str, Any], specs.get(name) or {})
        enrichment = enrichment_of(spec, allow_synonyms=False, where=f"{where}.{name}") if spec else {}
        # The file is the source of truth, so dropping an entry from it must clear the stored enrichment.
        if enrichment:
            member["semanticEnrichment"] = enrichment
            applied.append(name)
        elif member.pop("semanticEnrichment", None) is not None:
            applied.append(f"{name} (cleared)")
    return applied


def enrich(parts: list[dict[str, str]], enrichment: dict[str, Any]) -> tuple[list[dict[str, str]], list[str]]:
    entity_specs = cast(dict[str, Any], enrichment.get("entityTypes", {}))
    relationship_specs = cast(dict[str, Any], enrichment.get("relationshipTypes", {}))
    seen_entities: set[str] = set()
    seen_relationships: set[str] = set()
    applied: list[str] = []
    updated: list[dict[str, str]] = []

    for part in parts:
        path = part["path"]
        is_entity = path.startswith("EntityTypes/") and path.endswith("/definition.json")
        is_relationship = path.startswith("RelationshipTypes/") and path.endswith("/definition.json")
        if not (is_entity or is_relationship):
            updated.append(part)
            continue

        body = decoded(part)
        name = str(body["name"])
        specs = entity_specs if is_entity else relationship_specs
        (seen_entities if is_entity else seen_relationships).add(name)
        spec = cast(dict[str, Any], specs.get(name) or {})

        changed: list[str] = []
        enrichment = enrichment_of(spec, allow_synonyms=is_entity, where=name) if spec else {}
        if enrichment:
            body["semanticEnrichment"] = enrichment
            changed.append(f"{name}: entity" if is_entity else f"{name}: relationship")
        elif carries_enrichment(body.get("semanticEnrichment")):
            # Omitting the field leaves what the ontology stored in place, so a clear has to be explicit.
            body["semanticEnrichment"] = {}
            changed.append(f"{name}: cleared")
        for field in ("properties", "timeseriesProperties"):
            members = cast(list[dict[str, Any]], body.get(field) or [])
            member_specs = cast(dict[str, Any], spec.get(field) or {})
            names = enrich_members(members, member_specs, where=f"{name}.{field}")
            changed.extend(f"{name}.{field}: {member}" for member in names)

        applied.extend(changed)
        updated.append(encoded(part, body) if changed else part)

    unknown_entities = set(entity_specs) - seen_entities
    unknown_relationships = set(relationship_specs) - seen_relationships
    if unknown_entities or unknown_relationships:
        raise ValueError(
            f"not in the ontology: entity types {sorted(unknown_entities)}, "
            f"relationship types {sorted(unknown_relationships)}"
        )
    return updated, applied


def schema_preview(parts: list[dict[str, str]]) -> str:
    """The exact string the API turns the ontology into, so the budget cannot fail at runtime."""
    values = [
        decoded(part)
        for part in parts
        if part["path"].startswith("EntityTypes/") and part["path"].endswith("/definition.json")
    ]
    return ontology_schema({"isError": False, "structuredContent": {"values": values}})


def wait_for(response: httpx.Response, bearer: str, *, what: str) -> str | None:
    """Definition calls are long running: 200 is already the answer, 202 must be polled."""
    operation = response.headers.get("x-ms-operation-id", "")
    if not operation:
        print(f"{what}: accepted without an x-ms-operation-id header")
        return None
    delay = retry_after(response, 5)
    for _ in range(POLL_LIMIT):
        time.sleep(delay)
        poll = call("GET", f"{FABRIC}/v1/operations/{operation}", bearer)
        if poll.status_code >= 400:
            print(f"{what}: polling failed: {poll.status_code} {poll.text[:400]}")
            return None
        body = cast(dict[str, Any], poll.json()) if poll.content else {}
        status = str(body.get("status", ""))
        if status == "Succeeded":
            return operation
        if status == "Failed":
            print(f"{what} failed: {poll.text[:400]}")
            return None
        delay = retry_after(poll, delay)
    print(f"{what} is still running; check the item in Fabric")
    return None


def retry_after(response: httpx.Response, fallback: int) -> int:
    header = response.headers.get("Retry-After", "")
    return int(header) if header.isdigit() else fallback


def parts_of(payload: object, *, what: str) -> list[dict[str, str]] | None:
    parts: object = None
    if isinstance(payload, Mapping):
        definition = cast(Mapping[str, object], payload).get("definition")
        if isinstance(definition, Mapping):
            parts = cast(Mapping[str, object], definition).get("parts")
    if not isinstance(parts, list):
        print(f"{what}: the response carried no definition parts")
        return None
    return cast(list[dict[str, str]], parts)


def read_parts(item: str, bearer: str) -> list[dict[str, str]] | None:
    response = call("POST", f"{item}/getDefinition", bearer)
    if response.status_code == 200:
        return parts_of(response.json(), what="getDefinition")
    if response.status_code != 202:
        print(f"getDefinition failed: {response.status_code} {response.text[:400]}")
        return None
    operation = wait_for(response, bearer, what="getDefinition")
    if operation is None:
        return None
    result = call("GET", f"{FABRIC}/v1/operations/{operation}/result", bearer)
    if result.status_code >= 400:
        print(f"getDefinition failed: {result.status_code} {result.text[:400]}")
        return None
    return parts_of(result.json(), what="getDefinition")


def write_parts(item: str, bearer: str, parts: list[dict[str, str]]) -> bool:
    response = call("POST", f"{item}/updateDefinition", bearer, {"definition": {"parts": parts}})
    if response.status_code == 200:
        return True
    if response.status_code != 202:
        print(f"updateDefinition failed: {response.status_code} {response.text[:400]}")
        return False
    return wait_for(response, bearer, what="updateDefinition") is not None


def report(parts: list[dict[str, str]]) -> None:
    for part in parts:
        path = part["path"]
        if not path.endswith("/definition.json") or path == "definition.json":
            continue
        body = decoded(part)
        stored = cast(dict[str, Any] | None, body.get("semanticEnrichment"))
        if stored is None:
            continue
        synonyms = cast(list[str], stored.get("synonyms") or [])
        attributes = cast(dict[str, str], stored.get("customAttributes") or {})
        print(f"  {body['name']}: {len(synonyms)} synonyms, {len(attributes)} attributes")
        for field in ("properties", "timeseriesProperties"):
            for member in cast(list[dict[str, Any]], body.get(field) or []):
                member_enrichment = cast(dict[str, Any] | None, member.get("semanticEnrichment"))
                if member_enrichment is None:
                    continue
                member_attributes = cast(dict[str, str], member_enrichment.get("customAttributes") or {})
                print(f"    .{member['name']}: {len(member_attributes)} attributes")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--ontology", required=True)
    parser.add_argument("--enrichment", type=Path, default=Path("config/lamna-ontology-enrichment.json"))
    parser.add_argument("--backup", type=Path, default=Path("ontology-definition-backup.json"))
    parser.add_argument("--apply", action="store_true", help="write the change; otherwise dry run")
    arguments = parser.parse_args()

    enrichment = cast(dict[str, Any], json.loads(arguments.enrichment.read_text(encoding="utf-8")))
    bearer = token(FABRIC)
    item = f"{FABRIC}/v1/workspaces/{arguments.workspace}/items/{arguments.ontology}"
    parts = read_parts(item, bearer)
    if parts is None:
        return 1
    arguments.backup.write_text(json.dumps({"definition": {"parts": parts}}, indent=1), encoding="utf-8")
    print(f"backed up {len(parts)} parts to {arguments.backup}")

    updated, applied = enrich(parts, enrichment)
    print(f"enriching {len(applied)} objects:")
    for entry in applied:
        print(f"  - {entry}")

    preview = schema_preview(updated)
    print(f"\nagent-visible schema: {len(preview)} of {MAX_ONTOLOGY_SCHEMA_CHARS} characters")
    print(preview)

    if not arguments.apply:
        print("\ndry run only; pass --apply to write")
        return 0

    if not write_parts(item, bearer, updated):
        return 1

    stored = read_parts(item, bearer)
    if stored is None:
        print("written, but the re-read failed")
        return 1
    print("\nwritten; the ontology now stores:")
    report(stored)
    return 0


if __name__ == "__main__":
    sys.exit(main())
