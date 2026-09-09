from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol, cast

from eda_worker.fabric.ontology.config import OntologyTarget
from eda_worker.fabric.ontology.contracts import FabricOntologyQueryOperation, OntologyQueryPurpose
from eda_worker.fabric.ontology.grounding import (
    GroundingCacheEntry,
    NormalizedSchema,
    OntologyGroundingCache,
    normalize_ontology_schema,
)
from eda_worker.fabric.ontology.mcp_client import JsonValue, json_value

_DIGEST_PATTERN = re.compile(r"^[a-f0-9]{64}$")


class OntologyMcpOperations(Protocol):
    async def inspect(self) -> JsonValue: ...

    async def search(self, *, question: str) -> JsonValue: ...

    async def inspect_and_search(self, *, question: str) -> tuple[JsonValue, JsonValue]: ...


type OntologyClientFactory = Callable[[OntologyTarget], OntologyMcpOperations]


@dataclass(frozen=True)
class OntologyGatewayResult:
    value: JsonValue
    grounding: NormalizedSchema
    grounding_digest: str


class FabricOntologyGateway:
    def __init__(
        self,
        *,
        catalog: Mapping[str, OntologyTarget],
        client_factory: OntologyClientFactory,
        grounding_cache: OntologyGroundingCache | None = None,
        expected_grounding_digests: Mapping[str, str] | None = None,
    ) -> None:
        self._catalog = dict(catalog)
        self._client_factory = client_factory
        self._grounding_cache = grounding_cache or OntologyGroundingCache()
        self._expected_grounding_digests = dict(expected_grounding_digests or {})

    def clear_task(self, owner_partition_key: str, task_id: str) -> None:
        self._grounding_cache.clear_task(owner_partition_key, task_id)

    async def query(
        self,
        *,
        owner_partition_key: str,
        task_id: str,
        provider_contract_digest: str,
        operation: FabricOntologyQueryOperation,
    ) -> OntologyGatewayResult:
        if not _DIGEST_PATTERN.fullmatch(provider_contract_digest):
            raise ValueError("provider contract digest is malformed")
        target = self._catalog.get(operation.ontology)
        if target is None:
            raise ValueError("requested ontology is not a configured ontology")
        cache_key = (owner_partition_key, task_id, operation.ontology, provider_contract_digest)
        client = self._client_factory(target)

        if operation.purpose is OntologyQueryPurpose.SCHEMA:
            grounding = normalize_ontology_schema(schema_mapping(await client.inspect()))
            entry = GroundingCacheEntry(grounding=grounding, digest=grounding_digest(grounding))
            self._validate_grounding(operation.ontology, entry.digest)
            self._grounding_cache.put(cache_key, entry)
            return OntologyGatewayResult(
                value=json_value(grounding), grounding=grounding, grounding_digest=entry.digest
            )

        entry = self._grounding_cache.get(cache_key)
        if entry is None:
            raw_schema, value = await client.inspect_and_search(question=operation.question)
            grounding = normalize_ontology_schema(schema_mapping(raw_schema))
            entry = GroundingCacheEntry(grounding=grounding, digest=grounding_digest(grounding))
            self._validate_grounding(operation.ontology, entry.digest)
            self._grounding_cache.put(cache_key, entry)
        else:
            value = await client.search(question=operation.question)
        return OntologyGatewayResult(value=value, grounding=entry.grounding, grounding_digest=entry.digest)

    def _validate_grounding(self, alias: str, digest: str) -> None:
        expected = self._expected_grounding_digests.get(alias)
        if expected is not None and expected != digest:
            raise ValueError("ontology grounding drifted from the ready provider contract")


def grounding_digest(grounding: NormalizedSchema) -> str:
    return hashlib.sha256(json.dumps(grounding, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def schema_mapping(value: JsonValue) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("ontology schema response must be a JSON object")
    return cast(Mapping[str, object], value)
