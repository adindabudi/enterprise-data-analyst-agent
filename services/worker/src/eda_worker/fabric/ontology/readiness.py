from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from eda_worker.fabric.contracts import FabricSourceGuide
from eda_worker.fabric.ontology.config import ONTOLOGY_ENDPOINT_TEMPLATE, OntologyTarget
from eda_worker.fabric.ontology.mcp_client import (
    EXPECTED_ONTOLOGY_TOOLS,
    EXPECTED_TOOL_DESCRIPTIONS,
    validate_ontology_tools,
)
from eda_worker.fabric.readiness import FabricModelIdentity, FabricReadiness, FabricReadinessStatus


@dataclass(frozen=True)
class OntologyToolManifest:
    tool_names: tuple[str, ...]
    contract_digest: str


class OntologyReadinessModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class OntologyAliasRuntimeContract(OntologyReadinessModel):
    alias: str = Field(pattern=r"^[a-z][a-z0-9-]{1,39}$")
    target_pair_sha256: str = Field(alias="targetPairSha256", pattern=r"^[a-f0-9]{64}$")
    grounding_sha256: str = Field(alias="groundingSha256", pattern=r"^[a-f0-9]{64}$")
    grounding: dict[str, object]
    source_guide_sha256: str = Field(alias="sourceGuideSha256", pattern=r"^[a-f0-9]{64}$")
    source_guide: dict[str, object] = Field(alias="sourceGuide")

    @model_validator(mode="after")
    def validate_guide(self) -> OntologyAliasRuntimeContract:
        if _sha256_json(self.grounding) != self.grounding_sha256:
            raise ValueError("ontology grounding digest does not match")
        if _sha256_json(self.source_guide) != self.source_guide_sha256:
            raise ValueError("ontology source-guide digest does not match")
        if self.source_guide.get("alias") != self.alias:
            raise ValueError("ontology source guide alias does not match")
        return self

    def model_guide(self) -> FabricSourceGuide:
        terms = self.source_guide.get("routingTerms")
        if not isinstance(terms, list):
            raise ValueError("ontology source guide routing terms are malformed")
        raw_terms = cast(list[object], terms)
        if not all(isinstance(term, str) for term in raw_terms):
            raise ValueError("ontology source guide routing terms are malformed")
        description = self.source_guide.get("description")
        if not isinstance(description, str):
            raise ValueError("ontology source guide description is malformed")
        return FabricSourceGuide(
            alias=self.alias,
            description=description,
            vocabulary=tuple(cast(list[str], raw_terms)),
        )


class OntologyProviderRuntimeContract(OntologyReadinessModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    provider: Literal["ontology"] = "ontology"
    auth_contract_sha256: str = Field(alias="authContractSha256", pattern=r"^[a-f0-9]{64}$")
    deployment_sha256: str = Field(alias="deploymentSha256", pattern=r"^[a-f0-9]{64}$")
    endpoint_template_sha256: str = Field(alias="endpointTemplateSha256", pattern=r"^[a-f0-9]{64}$")
    target_catalog_sha256: str = Field(alias="targetCatalogSha256", pattern=r"^[a-f0-9]{64}$")
    scope_sha256: str = Field(alias="scopeSha256", pattern=r"^[a-f0-9]{64}$")
    audience_sha256: str = Field(alias="audienceSha256", pattern=r"^[a-f0-9]{64}$")
    tool_contract_sha256: str = Field(alias="toolContractSha256", pattern=r"^[a-f0-9]{64}$")
    tool_names: tuple[str, ...] = Field(alias="toolNames", min_length=2, max_length=2)
    aliases: tuple[OntologyAliasRuntimeContract, ...] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def validate_fixed_contract(self) -> OntologyProviderRuntimeContract:
        expected_endpoint = hashlib.sha256(ONTOLOGY_ENDPOINT_TEMPLATE.encode()).hexdigest()
        if self.endpoint_template_sha256 != expected_endpoint:
            raise ValueError("ontology endpoint template hash does not match")
        manifest = build_tool_manifest(
            [
                {
                    "name": name,
                    "description": EXPECTED_TOOL_DESCRIPTIONS[name],
                    "inputSchema": EXPECTED_ONTOLOGY_TOOLS[name],
                }
                for name in sorted(EXPECTED_TOOL_DESCRIPTIONS)
            ]
        )
        if self.tool_names != manifest.tool_names or self.tool_contract_sha256 != manifest.contract_digest:
            raise ValueError("ontology tool contract does not match")
        aliases = [alias.alias for alias in self.aliases]
        if aliases != sorted(set(aliases)):
            raise ValueError("ontology contract aliases are duplicated or unsorted")
        return self


class OntologyFeatureRecord(FabricModelIdentity):
    id: Literal["feature:fabric-ontology"] = "feature:fabric-ontology"
    provider: Literal["ontology"] = "ontology"
    state: Literal["configured", "ready", "failed"]
    deployment_id: str = Field(alias="deploymentId", min_length=1, max_length=128)
    provider_contract_sha256: str = Field(alias="providerContractSha256", pattern=r"^[a-f0-9]{64}$")
    acceptance_evidence_sha256: str | None = Field(
        default=None,
        alias="acceptanceEvidenceSha256",
        pattern=r"^[a-f0-9]{64}$",
    )
    verified_at: datetime = Field(alias="verifiedAt")


class OntologyRuntimeContainer(Protocol):
    async def read_item(self, item: str, partition_key: str) -> Mapping[str, object]: ...


@dataclass(frozen=True)
class OntologyRuntimeState:
    readiness: FabricReadiness
    contract: OntologyProviderRuntimeContract | None


def build_tool_manifest(tools: Sequence[Mapping[str, object]]) -> OntologyToolManifest:
    normalized_schemas = validate_ontology_tools(tools)
    tool_names = tuple(sorted(normalized_schemas))
    normalized_tools = [
        {
            "name": name,
            "description": EXPECTED_TOOL_DESCRIPTIONS[name],
            "inputSchema": normalized_schemas[name],
        }
        for name in tool_names
    ]
    payload = json.dumps(normalized_tools, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return OntologyToolManifest(
        tool_names=tool_names,
        contract_digest=hashlib.sha256(payload).hexdigest(),
    )


def ontology_provider_contract_sha256(contract: OntologyProviderRuntimeContract) -> str:
    return _sha256_json(cast(dict[str, object], contract.model_dump(mode="json", by_alias=True)))


async def load_ontology_runtime_state(
    container: OntologyRuntimeContainer,
    *,
    enabled: bool,
    catalog: Mapping[str, OntologyTarget],
    active_model: FabricModelIdentity,
    deployment_id: str,
) -> OntologyRuntimeState:
    if not enabled:
        return OntologyRuntimeState(FabricReadiness(status=FabricReadinessStatus.DISABLED), None)
    try:
        feature_raw = _clean(await container.read_item("feature:fabric-ontology", "feature:fabric-ontology"))
        feature = OntologyFeatureRecord.model_validate(feature_raw)
        contract_id = f"feature-contract:fabric-ontology:{feature.provider_contract_sha256}"
        immutable = _clean(await container.read_item(contract_id, contract_id))
        if (
            immutable.get("id") != contract_id
            or immutable.get("provider") != "ontology"
            or immutable.get("immutable") is not True
            or immutable.get("providerContractSha256") != feature.provider_contract_sha256
        ):
            return _failed_state()
        contract = OntologyProviderRuntimeContract.model_validate(immutable.get("contract"))
        if ontology_provider_contract_sha256(contract) != feature.provider_contract_sha256:
            return _failed_state()
        if {alias.alias for alias in contract.aliases} != set(catalog):
            return _failed_state()
        for alias_contract in contract.aliases:
            target = catalog[alias_contract.alias]
            guide = alias_contract.model_guide()
            if guide.description != target.description:
                return _failed_state()
        if feature.state == "failed" or feature.deployment_id != deployment_id:
            return _failed_state()
        feature_identity = FabricModelIdentity(
            modelProfile=feature.model_profile,
            modelDeployment=feature.model_deployment,
            servedModel=feature.served_model,
            servedSnapshot=feature.served_snapshot,
            promptVersion=feature.prompt_version,
            promptSha256=feature.prompt_sha256,
            requestOptionsSha256=feature.request_options_sha256,
        )
        status = (
            FabricReadinessStatus.READY
            if feature.state == "ready"
            and feature.acceptance_evidence_sha256 is not None
            and feature_identity == active_model
            else FabricReadinessStatus.CONFIGURED
        )
        return OntologyRuntimeState(FabricReadiness(status=status), contract)
    except Exception:
        # The caller turns FAILED into a startup abort, so without this the container crash-loops
        # on a generic sentence and the actual cause is never stated anywhere.
        logging.getLogger(__name__).exception("ontology readiness could not be evaluated")
        return _failed_state()


def _sha256_json(value: Mapping[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def _clean(value: Mapping[str, object]) -> dict[str, object]:
    return {key: item for key, item in value.items() if not key.startswith("_")}


def _failed_state() -> OntologyRuntimeState:
    return OntologyRuntimeState(FabricReadiness(status=FabricReadinessStatus.FAILED), None)
