from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol, cast

from azure.cosmos.exceptions import CosmosHttpResponseError
from eda_contracts import ArtifactKind, ArtifactRef
from eda_runtime_state.models import OperationRecord, OperationStatus, TaskRecord
from pydantic import BaseModel

from eda_worker.fabric.contracts import FabricPrincipal, FabricQueryResult
from eda_worker.fabric.gateway import ArtifactFabricResultStore
from eda_worker.fabric.ontology.config import OntologyTarget
from eda_worker.fabric.ontology.contracts import FabricOntologyQueryOperation, OntologyQueryPurpose
from eda_worker.fabric.ontology.gateway import FabricOntologyGateway, OntologyGatewayResult
from eda_worker.fabric.ontology.grounding import describe_grounding
from eda_worker.fabric.ontology.provenance import FabricOntologyQueryDocument


class OntologyRuntimeRepository(Protocol):
    async def resolve_task(self, task_id: str) -> TaskRecord | None: ...

    async def begin_operation(
        self,
        task: TaskRecord,
        step_name: str,
        canonical_input: dict[str, Any],
    ) -> OperationRecord: ...

    async def complete_operation(
        self,
        task: TaskRecord,
        operation_id: str,
        immutable_result_ref: str,
    ) -> OperationRecord: ...


class OntologyArtifactStore(Protocol):
    async def persist_bytes(
        self,
        task_id: str,
        kind: ArtifactKind,
        display_name: str,
        content: bytes,
    ) -> ArtifactRef: ...

    async def read_bytes(self, task_id: str, artifact: ArtifactRef) -> bytes: ...


class OntologyQueryRepository(Protocol):
    async def put(self, document: FabricOntologyQueryDocument) -> FabricOntologyQueryDocument: ...


class FabricOntologyCapabilityGateway:
    def __init__(
        self,
        *,
        catalog: dict[str, OntologyTarget],
        gateway_factory: Callable[[FabricPrincipal], FabricOntologyGateway],
        runtime: OntologyRuntimeRepository,
        artifacts: OntologyArtifactStore,
        evidence: OntologyQueryRepository,
        provider_contract_digest: str,
    ) -> None:
        self._catalog = dict(catalog)
        self._gateway_factory = gateway_factory
        self._runtime = runtime
        self._artifacts = artifacts
        self._evidence = evidence
        self._provider_contract_digest = provider_contract_digest
        self._gateways: dict[tuple[str, str], FabricOntologyGateway] = {}
        self._results = ArtifactFabricResultStore(artifacts)

    async def query(
        self,
        task_id: str,
        invocation_id: str,
        principal: FabricPrincipal,
        operation: BaseModel,
    ) -> FabricQueryResult:
        request = FabricOntologyQueryOperation.model_validate(operation.model_dump(mode="python"))
        target = self._catalog.get(request.ontology)
        if target is None:
            raise ValueError("requested ontology is not configured")
        task = await self._runtime.resolve_task(task_id)
        if task is None:
            raise ValueError("ontology task is unavailable")
        if task.tenant_id != principal.tenant_id or task.owner_object_id != principal.owner_object_id:
            raise ValueError("ontology task owner does not match the trusted principal")
        if task.cancellation_requested:
            return FabricQueryResult(status="cancelled", summary="Task cancellation was requested.")
        canonical_input = {
            "invocationId": invocation_id,
            "providerContractSha256": self._provider_contract_digest,
            "operation": request.model_dump(mode="json"),
        }
        started = await self._runtime.begin_operation(task, "fabric.ontology.query", canonical_input)
        if started.status is OperationStatus.COMPLETED:
            if started.immutable_result_ref is None:
                raise ValueError("completed ontology operation has no immutable result")
            return await self._results.load(task, started.immutable_result_ref)

        started_at = datetime.now(UTC)
        owner_key = (str(principal.tenant_id), str(principal.owner_object_id))
        gateway = self._gateways.get(owner_key)
        if gateway is None:
            gateway = self._gateway_factory(principal)
            self._gateways[owner_key] = gateway
        owner_partition_key = "/".join((*owner_key, task.session_id))
        provider_result = await gateway.query(
            owner_partition_key=owner_partition_key,
            task_id=task.id,
            provider_contract_digest=self._provider_contract_digest,
            operation=request,
        )
        result_bytes = json.dumps(
            provider_result.value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        result_artifact = await self._artifacts.persist_bytes(
            task.id,
            ArtifactKind.DATA,
            "fabric-ontology-result.json",
            result_bytes,
        )
        result_sha256 = hashlib.sha256(result_bytes).hexdigest()
        if result_artifact.sha256 != result_sha256:
            raise ValueError("ontology result artifact digest mismatch")
        query_ref = _query_ref(task.id, invocation_id)
        await self._evidence.put(
            FabricOntologyQueryDocument(
                tenantId=task.tenant_id,
                ownerObjectId=task.owner_object_id,
                sessionId=task.session_id,
                queryRef=query_ref,
                sourceAlias=request.ontology,
                workspaceId=target.workspace_id,
                ontologyId=target.ontology_id,
                purpose=request.purpose,
                question=request.question,
                providerContractDigest=self._provider_contract_digest,
                entitySchemaDigest=provider_result.grounding_digest,
                resultArtifactRef=result_artifact.artifact_id,
                resultSha256=result_sha256,
                resultShape=_result_shape(provider_result),
                reconciliationStatus="not_required",
                startedAt=started_at,
                completedAt=datetime.now(UTC),
            )
        )
        result = FabricQueryResult(
            status="ok",
            summary=_summary(request.purpose, provider_result),
            query_ref=query_ref,
            artifact_refs=(result_artifact,),
        )
        immutable_result_ref = await self._results.persist(task, result)
        await self._runtime.complete_operation(task, started.id, immutable_result_ref)
        return result

    async def clear_task(self, principal: FabricPrincipal, task_id: str) -> None:
        owner_key = (str(principal.tenant_id), str(principal.owner_object_id))
        gateway = self._gateways.get(owner_key)
        task = await self._runtime.resolve_task(task_id)
        if gateway is not None and task is not None:
            gateway.clear_task("/".join((*owner_key, task.session_id)), task_id)


class CosmosOntologyQueryRepository:
    def __init__(self, workspace: Any) -> None:
        self._workspace = workspace

    async def put(self, document: FabricOntologyQueryDocument) -> FabricOntologyQueryDocument:
        body = {
            "id": document.query_ref,
            "recordType": "fabricOntologyQuery",
            **document.model_dump(mode="json", by_alias=True),
        }
        try:
            stored = await self._workspace.create_item(body)
        except CosmosHttpResponseError as error:
            if error.status_code != 409:
                raise
            stored = await self._workspace.read_item(
                item=document.query_ref,
                partition_key=[str(document.tenant_id), str(document.owner_object_id), document.session_id],
            )
            existing = FabricOntologyQueryDocument.model_validate(_evidence_payload(stored))
            if existing != document:
                raise ValueError("ontology query evidence conflict") from error
            return existing
        return FabricOntologyQueryDocument.model_validate(_evidence_payload(stored))


def _query_ref(task_id: str, invocation_id: str) -> str:
    digest = hashlib.sha256(f"{task_id}\0{invocation_id}".encode()).hexdigest()
    return f"fabric-query-{digest[:32]}"


def _summary(purpose: OntologyQueryPurpose, result: OntologyGatewayResult) -> str:
    if purpose is OntologyQueryPurpose.SCHEMA:
        return f"Schema of the configured source. {describe_grounding(result.grounding)}"
    return "Fabric ontology query completed with structured evidence."


def _result_shape(result: OntologyGatewayResult) -> str:
    if isinstance(result.value, dict):
        return "object"
    if isinstance(result.value, list):
        return "array"
    return "scalar"


def _evidence_payload(value: Any) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("ontology query evidence document is malformed")
    raw = cast(dict[object, object], value)
    return {
        str(key): item for key, item in raw.items() if key not in {"id", "recordType"} and not str(key).startswith("_")
    }
