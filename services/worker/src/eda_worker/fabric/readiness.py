from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from importlib.metadata import version
from typing import Literal, Protocol, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from eda_worker.fabric.config import FABRIC_IQ_MCP_URL, FABRIC_IQ_VARIANT
from eda_worker.fabric.mcp_client import RUNTIME_TOOL_NAMES, validate_fabric_tools

SHA256_PATTERN = r"^[a-f0-9]{64}$"
MAX_CONTRACT_AGE = timedelta(hours=24)


class ReadinessModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class FabricToolContract(ReadinessModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=8_000)
    input_schema: dict[str, object] = Field(alias="inputSchema")
    schema_sha256: str = Field(alias="schemaSha256", pattern=SHA256_PATTERN)


class FabricProviderContract(ReadinessModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    endpoint_sha256: str = Field(alias="endpointSha256", pattern=SHA256_PATTERN)
    routing_header_sha256: str = Field(alias="routingHeaderSha256", pattern=SHA256_PATTERN)
    mcp_version: str = Field(alias="mcpVersion", min_length=1, max_length=64)
    agent_framework_core_version: str = Field(alias="agentFrameworkCoreVersion", min_length=1, max_length=64)
    agent_framework_foundry_version: str = Field(alias="agentFrameworkFoundryVersion", min_length=1, max_length=64)
    tools: tuple[FabricToolContract, ...] = Field(min_length=6, max_length=6)
    tool_names: tuple[str, ...] = Field(alias="toolNames", min_length=6, max_length=6)
    tool_schema_hashes: dict[str, str] = Field(alias="toolSchemaHashes", min_length=6, max_length=6)
    runtime_allowlist: tuple[str, ...] = Field(alias="runtimeAllowlist", min_length=3, max_length=3)
    model_deployment: str = Field(alias="modelDeployment", min_length=1, max_length=128)
    base_model: str = Field(alias="baseModel", min_length=1, max_length=128)
    fabric_tenant_sha256: str = Field(alias="fabricTenantSha256", pattern=SHA256_PATTERN)
    probed_at: datetime = Field(alias="probedAt")
    deployment_id: str = Field(alias="deploymentId", min_length=1, max_length=128)
    run_id: str = Field(alias="runId", min_length=1, max_length=128)


class FabricReadinessStatus(StrEnum):
    DISABLED = "disabled"
    FAILED = "failed"
    CONFIGURED = "configured"
    READY = "ready"


class FabricModelIdentity(ReadinessModel):
    model_profile: str = Field(alias="modelProfile", min_length=1, max_length=80)
    model_deployment: str = Field(alias="modelDeployment", min_length=1, max_length=128)
    served_model: str = Field(alias="servedModel", min_length=1, max_length=128)
    served_snapshot: str | None = Field(default=None, alias="servedSnapshot", max_length=64)
    prompt_version: str = Field(alias="promptVersion", min_length=1, max_length=128)
    prompt_sha256: str = Field(alias="promptSha256", pattern=SHA256_PATTERN)
    request_options_sha256: str = Field(alias="requestOptionsSha256", pattern=SHA256_PATTERN)


class FabricFeatureRecord(FabricModelIdentity):
    id: Literal["feature:fabric"] = "feature:fabric"
    provider: Literal["semantic_model"] = "semantic_model"
    state: Literal["configured", "ready", "failed"]
    deployment_id: str = Field(alias="deploymentId", min_length=1, max_length=128)
    provider_contract_sha256: str = Field(alias="providerContractSha256", pattern=SHA256_PATTERN)
    acceptance_evidence_sha256: str | None = Field(
        default=None,
        alias="acceptanceEvidenceSha256",
        pattern=SHA256_PATTERN,
    )
    verified_at: datetime = Field(alias="verifiedAt")


class FabricReadiness(ReadinessModel):
    status: FabricReadinessStatus


class FabricRuntimeContainer(Protocol):
    async def read_item(self, item: str, partition_key: str) -> Mapping[str, object]: ...


@dataclass(frozen=True)
class FabricRuntimeState:
    readiness: FabricReadiness
    contract: FabricProviderContract | None


def build_provider_contract(
    *,
    tools: Sequence[Mapping[str, object]],
    expected_tools: Sequence[Mapping[str, object]],
    fabric_tenant_id: UUID,
    model_deployment: str,
    base_model: str,
    deployment_id: str,
    run_id: str,
    probed_at: datetime,
) -> FabricProviderContract:
    normalized = validate_fabric_tools(tools, expected_tools)
    tool_contracts: list[FabricToolContract] = []
    schema_hashes: dict[str, str] = {}
    for name, tool in normalized.items():
        schema = cast(dict[str, object], tool["inputSchema"])
        schema_hash = _sha256_json(schema)
        schema_hashes[name] = schema_hash
        tool_contracts.append(
            FabricToolContract(
                name=name,
                description=cast(str, tool["description"]),
                inputSchema=schema,
                schemaSha256=schema_hash,
            )
        )
    contract = FabricProviderContract(
        endpointSha256=_sha256_text(FABRIC_IQ_MCP_URL),
        routingHeaderSha256=_sha256_json({"X-VARIANTS": FABRIC_IQ_VARIANT}),
        mcpVersion=version("mcp"),
        agentFrameworkCoreVersion=version("agent-framework-core"),
        agentFrameworkFoundryVersion=version("agent-framework-foundry"),
        tools=tuple(tool_contracts),
        toolNames=tuple(normalized),
        toolSchemaHashes=schema_hashes,
        runtimeAllowlist=tuple(sorted(RUNTIME_TOOL_NAMES)),
        modelDeployment=model_deployment,
        baseModel=base_model,
        fabricTenantSha256=_sha256_text(str(fabric_tenant_id)),
        probedAt=probed_at,
        deploymentId=deployment_id,
        runId=run_id,
    )
    _ensure_sanitized(contract)
    return contract


def validate_runtime_contract(
    contract: FabricProviderContract,
    *,
    expected_tools: Sequence[Mapping[str, object]],
    fabric_tenant_id: UUID,
    model_deployment: str,
    base_model: str,
    deployment_id: str,
    now: datetime,
) -> FabricProviderContract:
    actual_tools = [
        {
            "name": tool.name,
            "description": tool.description,
            "inputSchema": tool.input_schema,
        }
        for tool in contract.tools
    ]
    validate_fabric_tools(actual_tools, expected_tools)
    expected_values = {
        "endpoint hash": (contract.endpoint_sha256, _sha256_text(FABRIC_IQ_MCP_URL)),
        "routing header hash": (
            contract.routing_header_sha256,
            _sha256_json({"X-VARIANTS": FABRIC_IQ_VARIANT}),
        ),
        "MCP package": (contract.mcp_version, version("mcp")),
        "Agent Framework core package": (
            contract.agent_framework_core_version,
            version("agent-framework-core"),
        ),
        "Agent Framework Foundry package": (
            contract.agent_framework_foundry_version,
            version("agent-framework-foundry"),
        ),
        "Fabric tenant": (contract.fabric_tenant_sha256, _sha256_text(str(fabric_tenant_id))),
        "model deployment": (contract.model_deployment, model_deployment),
        "base model": (contract.base_model, base_model),
        "deployment": (contract.deployment_id, deployment_id),
    }
    for label, (actual, expected) in expected_values.items():
        if actual != expected:
            raise ValueError(f"Fabric provider contract {label} does not match the active deployment")
    if contract.probed_at > now + timedelta(minutes=5) or now - contract.probed_at > MAX_CONTRACT_AGE:
        raise ValueError("Fabric provider contract is stale")
    if contract.tool_names != tuple(tool.name for tool in contract.tools):
        raise ValueError("Fabric provider contract tool order is invalid")
    if contract.runtime_allowlist != tuple(sorted(RUNTIME_TOOL_NAMES)):
        raise ValueError("Fabric provider contract runtime allowlist is invalid")
    for tool in contract.tools:
        if contract.tool_schema_hashes.get(tool.name) != tool.schema_sha256:
            raise ValueError("Fabric provider contract schema hash is invalid")
        if tool.schema_sha256 != _sha256_json(tool.input_schema):
            raise ValueError("Fabric provider contract schema content is invalid")
    _ensure_sanitized(contract)
    return contract


def provider_contract_sha256(contract: FabricProviderContract) -> str:
    payload = contract.model_dump(mode="json", by_alias=True)
    return _sha256_json(cast(Mapping[str, object], payload))


def evaluate_fabric_readiness(
    *,
    enabled: bool,
    feature: FabricFeatureRecord | None,
    contract: FabricProviderContract | None,
    expected_tools: Sequence[Mapping[str, object]],
    fabric_tenant_id: UUID,
    active_model: FabricModelIdentity,
    deployment_id: str,
    now: datetime,
) -> FabricReadiness:
    if not enabled:
        return FabricReadiness(status=FabricReadinessStatus.DISABLED)
    if feature is None or contract is None:
        return FabricReadiness(status=FabricReadinessStatus.FAILED)
    try:
        validate_runtime_contract(
            contract,
            expected_tools=expected_tools,
            fabric_tenant_id=fabric_tenant_id,
            model_deployment=active_model.model_deployment,
            base_model=active_model.served_model,
            deployment_id=deployment_id,
            now=now,
        )
    except ValueError:
        return FabricReadiness(status=FabricReadinessStatus.FAILED)
    if (
        feature.state == "failed"
        or feature.deployment_id != deployment_id
        or feature.provider_contract_sha256 != provider_contract_sha256(contract)
    ):
        return FabricReadiness(status=FabricReadinessStatus.FAILED)
    if (
        feature.state == "ready"
        and feature.acceptance_evidence_sha256 is not None
        and _feature_identity(feature) == active_model
    ):
        return FabricReadiness(status=FabricReadinessStatus.READY)
    return FabricReadiness(status=FabricReadinessStatus.CONFIGURED)


async def load_fabric_runtime_state(
    container: FabricRuntimeContainer,
    *,
    enabled: bool,
    fabric_tenant_id: UUID | None,
    active_model: FabricModelIdentity,
    deployment_id: str,
    now: datetime,
) -> FabricRuntimeState:
    if not enabled:
        return FabricRuntimeState(
            readiness=FabricReadiness(status=FabricReadinessStatus.DISABLED),
            contract=None,
        )
    if fabric_tenant_id is None:
        return _failed_runtime_state()
    try:
        raw_feature = await container.read_item("feature:fabric", partition_key="feature:fabric")
        feature = FabricFeatureRecord.model_validate(_without_cosmos_metadata(raw_feature))
        contract_id = f"feature-contract:fabric:{feature.provider_contract_sha256}"
        raw_immutable = _without_cosmos_metadata(await container.read_item(contract_id, partition_key=contract_id))
        if (
            raw_immutable.get("id") != contract_id
            or raw_immutable.get("recordType") != "featureContract"
            or raw_immutable.get("provider") != "semantic_model"
            or raw_immutable.get("immutable") is not True
            or raw_immutable.get("providerContractSha256") != feature.provider_contract_sha256
        ):
            return _failed_runtime_state()
        contract = FabricProviderContract.model_validate(raw_immutable.get("contract"))
        if provider_contract_sha256(contract) != feature.provider_contract_sha256:
            return _failed_runtime_state()
        expected_tools = [
            {
                "name": tool.name,
                "description": tool.description,
                "inputSchema": tool.input_schema,
            }
            for tool in contract.tools
        ]
        readiness = evaluate_fabric_readiness(
            enabled=True,
            feature=feature,
            contract=contract,
            expected_tools=expected_tools,
            fabric_tenant_id=fabric_tenant_id,
            active_model=active_model,
            deployment_id=deployment_id,
            now=now,
        )
        if readiness.status is FabricReadinessStatus.FAILED:
            return _failed_runtime_state()
        return FabricRuntimeState(readiness=readiness, contract=contract)
    except Exception:
        # The caller turns FAILED into a startup abort, so without this the container crash-loops
        # on a generic sentence and the actual cause is never stated anywhere.
        logging.getLogger(__name__).exception("semantic Fabric readiness could not be evaluated")
        return _failed_runtime_state()


def _feature_identity(feature: FabricFeatureRecord) -> FabricModelIdentity:
    return FabricModelIdentity(
        modelProfile=feature.model_profile,
        modelDeployment=feature.model_deployment,
        servedModel=feature.served_model,
        servedSnapshot=feature.served_snapshot,
        promptVersion=feature.prompt_version,
        promptSha256=feature.prompt_sha256,
        requestOptionsSha256=feature.request_options_sha256,
    )


def _without_cosmos_metadata(value: Mapping[str, object]) -> dict[str, object]:
    return {key: item for key, item in value.items() if not key.startswith("_")}


def _failed_runtime_state() -> FabricRuntimeState:
    return FabricRuntimeState(
        readiness=FabricReadiness(status=FabricReadinessStatus.FAILED),
        contract=None,
    )


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_json(value: Mapping[str, object]) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return _sha256_text(payload)


def _ensure_sanitized(contract: FabricProviderContract) -> None:
    encoded = contract.model_dump_json(by_alias=True)
    forbidden = (FABRIC_IQ_MCP_URL, FABRIC_IQ_VARIANT, "Authorization", "Bearer ")
    if any(value in encoded for value in forbidden):
        raise ValueError("Fabric provider contract contains transport or credential data")
    if contract.probed_at.tzinfo is None or contract.probed_at.utcoffset() is None:
        raise ValueError("Fabric provider contract timestamp must be timezone-aware")
    if contract.probed_at.astimezone(UTC).year < 2024:
        raise ValueError("Fabric provider contract timestamp is invalid")
