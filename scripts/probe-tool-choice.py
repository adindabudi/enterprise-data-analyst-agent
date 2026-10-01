"""Which tool does the model reach for, and what does it write?

The deployed answer to "which ICU is above 75%" came back 21/21, 10/10, 10/10 --
the traversal-intersection trap. That has two very different causes: query_graph
was never offered, or it was offered and the model wrote GQL without OPTIONAL
MATCH. Guessing between them is how the wrong fix gets shipped.

This runs the production prompt and options against the real model with BOTH
tools carrying their production descriptions, backed by the real ontology graph.
It prints the tool chosen and the exact query sent.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID

from agent_framework import tool
from agent_framework.foundry import FoundryChatClient
from azure.identity.aio import AzureCliCredential
from eda_api.chat.model import load_interactive_model_config
from eda_api.chat.service import FABRIC_QUERY_DESCRIPTION, GRAPH_QUERY_DESCRIPTION
from eda_api.fabric_auth.graph import FabricGraphQueryService
from eda_api.fabric_auth.snapshot import RELATIONSHIP_QUERY, relationships_from_rows
from eda_api.fabric_ontology import OntologyTarget
from eda_runtime_state.models import TaskPartition

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKSPACE = "b82afbde-8304-44c0-ac94-3cf69f6da909"
ONTOLOGY = "5566b159-6998-4bb8-a167-6d5bf3c43352"
ALIAS = "lamna-healthcare"
SOURCE_DESCRIPTION = (
    "Synthetic hospital operations covering patients, rooms, departments, hospitals, "
    "equipment, and vital-sign telemetry."
)
SCHEMA = (
    "vitalsignequipment(EquipmentId, PatientId, RoomId, EquipmentType, MonitoringStartDate) "
    "with time series (HeartRate, OxygenSaturation, ReadingId, RespiratoryRate, Timestamp); "
    "rooms(RoomId, RoomNumber, DepartmentId, RoomType); "
    "hospitals(HospitalId, HospitalName, City, State); "
    "patients(PatientId, FirstName, LastName, DateOfBirth, AdmissionDate, CurrentRoomId); "
    "departments(DepartmentId, DepartmentName, HospitalId, Floor)"
)
PARTITION = TaskPartition(
    tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
    owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
    session_id="ses_probe_12345678",
)
CASES = [
    ("the failing one", "ICU mana yang okupansinya di atas ambang aman 75%?"),
    ("english", "Which ICU departments are above the 75% safe occupancy threshold?"),
]


class AzCliToken:
    async def acquire(self, partition: TaskPartition) -> str:
        del partition
        return await asyncio.to_thread(_token)


def _token() -> str:
    az = shutil.which("az")
    if az is None:
        raise RuntimeError("the Azure CLI is required")
    argv = [
        az,
        "account",
        "get-access-token",
        "--resource",
        "https://api.fabric.microsoft.com",
        "--query",
        "accessToken",
        "-o",
        "tsv",
    ]
    return subprocess.run(argv, capture_output=True, text=True, check=True).stdout.strip()  # noqa: S603


@dataclass
class Recorder:
    graph: list[str] = field(default_factory=list[str])
    fabric: list[str] = field(default_factory=list[str])


def graph_tool(recorder: Recorder, service: FabricGraphQueryService, relationships: str) -> Any:
    description = f"{GRAPH_QUERY_DESCRIPTION} Node labels and properties: {SCHEMA}."
    if relationships:
        description = f"{description} Relationships: {relationships}."

    @tool(name="query_graph", description=description, approval_mode="never_require")
    async def query_graph(query: str) -> dict[str, str]:
        recorder.graph.append(query)
        try:
            return {"status": "ok", "rows": await service.execute(PARTITION, query)}
        except Exception as error:
            return {"status": "error", "detail": str(error)}

    return query_graph


def fabric_tool(recorder: Recorder) -> Any:
    description = (
        f"{FABRIC_QUERY_DESCRIPTION} This deployment exposes exactly one source, "
        f"alias '{ALIAS}': {SOURCE_DESCRIPTION}. Its entity types and properties: {SCHEMA}."
    )

    @tool(name="query_fabric", description=description, approval_mode="never_require")
    async def query_fabric(source: str, question: str) -> dict[str, str]:
        del source
        recorder.fabric.append(question)
        return {"status": "error", "detail": "this source is temporarily unavailable"}

    return query_fabric


def merged_tool(recorder: Recorder, service: FabricGraphQueryService, relationships: str) -> Any:
    """One tool, because the prompt says 'answer only after query_fabric returns status ok'."""
    description = (
        f"Query the configured Fabric business data source, alias '{ALIAS}': {SOURCE_DESCRIPTION}. "
        "There are two ways to ask, and choosing the right one decides whether the number is correct. "
        "For every count, total, occupancy, ratio, threshold, ranking or per-group breakdown, pass "
        "graph_query with a GQL query you write yourself, and leave question empty. For vital-sign "
        "readings over time, which GQL cannot reach, pass question instead. "
        f"{GRAPH_QUERY_DESCRIPTION} Node labels and properties: {SCHEMA}. Relationships: {relationships}."
    )

    @tool(name="query_fabric", description=description, approval_mode="never_require")
    async def query_fabric(source: str, question: str = "", graph_query: str = "") -> dict[str, str]:
        del source
        if graph_query:
            recorder.graph.append(graph_query)
            try:
                return {"status": "ok", "rows": await service.execute(PARTITION, graph_query)}
            except Exception as error:
                return {"status": "error", "detail": str(error)}
        recorder.fabric.append(question)
        return {"status": "error", "detail": "the time-series path is temporarily unavailable"}

    return query_fabric


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--deployment", default="gpt-5.6-terra")
    parser.add_argument("--runs", type=int, default=1, help="repeat each case; tool choice is stochastic")
    parser.add_argument("--single-tool", action="store_true", help="merge both paths into one query_fabric")
    arguments = parser.parse_args()

    config = load_interactive_model_config(
        contract_path=REPO_ROOT / ".artifacts/model-contract.json",
        prompt_path=REPO_ROOT / "services/worker/src/eda_worker/agent/prompts/gpt-5.6-terra-v1.md",
        expected_deployment=arguments.deployment,
    )
    target = OntologyTarget.model_validate(
        {
            "workspaceId": WORKSPACE,
            "ontologyId": ONTOLOGY,
            "description": SOURCE_DESCRIPTION,
            "routingTerms": ["patient"],
        }
    )
    service = FabricGraphQueryService(target, token_provider=AzCliToken())
    introspected = await service.execute_evidence(PARTITION, RELATIONSHIP_QUERY)
    edges = relationships_from_rows(json.loads(introspected.complete))
    relationships = "; ".join(f"{edge.name} ({edge.source} -> {edge.target})" for edge in edges)
    print(f"relationships: {relationships}\n")

    async with AzureCliCredential() as credential:
        client = FoundryChatClient(
            project_endpoint=arguments.endpoint,
            model=config.deployment,
            credential=credential,
        )
        for label, question in CASES:
            for attempt in range(arguments.runs):
                recorder = Recorder()
                tools = (
                    (merged_tool(recorder, service, relationships),)
                    if arguments.single_tool
                    else (fabric_tool(recorder), graph_tool(recorder, service, relationships))
                )
                agent = client.as_agent(
                    name="tool-choice-probe",
                    instructions=config.instructions,
                    tools=tools,
                    default_options=config.options,
                )
                print(f"=== [{label} {attempt + 1}/{arguments.runs}] {question}")
                try:
                    result = await agent.run(question)
                except Exception as error:
                    print(f"    FAILED: {type(error).__name__}: {error}\n")
                    continue
                for sent in recorder.graph:
                    print(f"    query_graph  <- {sent}")
                for sent in recorder.fabric:
                    print(f"    query_fabric <- {sent}")
                if not recorder.graph and not recorder.fabric:
                    print("    called neither tool")
                print(f"    answer: {(result.text or '')[:700]}\n")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
