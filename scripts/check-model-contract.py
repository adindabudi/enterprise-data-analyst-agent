from __future__ import annotations

import argparse
import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from eda_worker.agent.prompt_loader import load_prompt
from eda_worker.model.client import create_foundry_client
from eda_worker.model.compatibility import (
    Candidate,
    Hosting,
    ModelContractMismatch,
    ProbeClient,
    ProbeResult,
    ToolProbeResult,
    profile_candidates,
    run_probe,
    run_tool_probe,
    select_contract,
)
from eda_worker.model.profiles import MODEL_PROFILES, ModelContract, ModelProfileId, VerifiedProfile, WorkClass


@dataclass(frozen=True)
class ProbeSettings:
    app_env: str
    foundry_project_endpoint: str
    foundry_model_deployment: str
    managed_identity_client_id: str | None


@dataclass(frozen=True)
class GateResult:
    contract: ModelContract
    probes: tuple[ProbeResult, ...]
    automatic_tools: ToolProbeResult
    forced_tools: ToolProbeResult


async def check_model_contract(
    *,
    project_endpoint: str,
    deployment: str,
    profile_id: ModelProfileId,
    hosting: Hosting,
    managed_identity_client_id: str | None,
) -> GateResult:
    profile = MODEL_PROFILES[profile_id]
    prompt = load_prompt(profile_id)
    settings = ProbeSettings(
        app_env="production" if managed_identity_client_id is not None else "development",
        foundry_project_endpoint=project_endpoint,
        foundry_model_deployment=deployment,
        managed_identity_client_id=managed_identity_client_id,
    )
    client, credential = create_foundry_client(settings, None)
    try:
        results: list[ProbeResult] = []
        for work_class in WorkClass:
            for candidate in profile_candidates(profile_id, work_class):
                results.append(
                    await run_probe(
                        cast(ProbeClient, client),
                        work_class,
                        candidate,
                        deployment=deployment,
                        model_profile=profile_id,
                        prompt=prompt,
                        hosting=hosting,
                    )
                )
        contract = select_contract(results, prompt, profile)
        analysis_candidate = Candidate(
            wire_shape=contract.wire_shape,
            options=contract.options_for(WorkClass.ANALYSIS),
        )
        automatic_tools = await run_tool_probe(
            client,
            analysis_candidate,
            prompt=prompt,
            forced_first=False,
        )
        forced_tools = await run_tool_probe(
            client,
            analysis_candidate,
            prompt=prompt,
            forced_first=True,
        )
        if not automatic_tools.accepted or not forced_tools.accepted:
            raise ModelContractMismatch("stateless Core tool routing is not compatible")
        contract = _attach_tool_evidence(contract, automatic_tools, forced_tools)
        _validate_sanitized_contract(contract, prompt.text)
        return GateResult(
            contract=contract,
            probes=tuple(results),
            automatic_tools=automatic_tools,
            forced_tools=forced_tools,
        )
    finally:
        await client.project_client.close()
        await credential.close()


def _attach_tool_evidence(
    contract: ModelContract,
    automatic_tools: ToolProbeResult,
    forced_tools: ToolProbeResult,
) -> ModelContract:
    verified_profiles = dict(contract.verified_profiles)
    analysis = verified_profiles[WorkClass.ANALYSIS]
    markers = {
        *(f"marker:{value}" for value in automatic_tools.evidence),
        *(f"marker:{value}" for value in forced_tools.evidence),
    }
    verified_profiles[WorkClass.ANALYSIS] = VerifiedProfile(
        request_options=analysis.request_options,
        response_evidence=tuple(sorted({*analysis.response_evidence, *markers})),
    )
    return contract.model_copy(update={"verified_profiles": verified_profiles})


def _validate_sanitized_contract(contract: ModelContract, prompt_text: str) -> None:
    artifact = contract.model_dump_json(by_alias=True, exclude_none=True)
    if prompt_text in artifact:
        raise ModelContractMismatch("contract artifact contains prompt content")
    forbidden = ("http://", "https://", "phase complete", "tool route complete", "encrypted_content", "protected_data")
    if any(value in artifact for value in forbidden):
        raise ModelContractMismatch("contract artifact contains forbidden response or endpoint data")


def write_contract(output: Path, contract: ModelContract) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            contract.model_dump_json(by_alias=True, exclude_none=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


def _environment_default(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--project-endpoint",
        default=_environment_default("EDA_FOUNDRY_PROJECT_ENDPOINT", "FOUNDRY_PROJECT_ENDPOINT", "PROJECT_ENDPOINT"),
    )
    parser.add_argument(
        "--deployment",
        default=_environment_default("EDA_FOUNDRY_MODEL_DEPLOYMENT", "FOUNDRY_MODEL", "MODEL_DEPLOYMENT_NAME"),
    )
    parser.add_argument(
        "--model-profile",
        default=_environment_default("EDA_MODEL_PROFILE", "MODEL_PROFILE"),
        choices=list(ModelProfileId),
    )
    parser.add_argument("--hosting", default="azure", choices=("azure", "anthropic"))
    parser.add_argument("--managed-identity-client-id", default=os.environ.get("EDA_MANAGED_IDENTITY_CLIENT_ID"))
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    if not arguments.project_endpoint:
        parser.error("--project-endpoint or EDA_FOUNDRY_PROJECT_ENDPOINT is required")
    if not arguments.deployment:
        parser.error("--deployment or EDA_FOUNDRY_MODEL_DEPLOYMENT is required")
    if not arguments.model_profile:
        parser.error("--model-profile or EDA_MODEL_PROFILE is required")
    return arguments


def main() -> None:
    arguments = parse_args()
    try:
        result = asyncio.run(
            check_model_contract(
                project_endpoint=cast(str, arguments.project_endpoint),
                deployment=cast(str, arguments.deployment),
                profile_id=ModelProfileId(cast(str, arguments.model_profile)),
                hosting=cast(Hosting, arguments.hosting),
                managed_identity_client_id=cast(str | None, arguments.managed_identity_client_id),
            )
        )
        write_contract(arguments.output, result.contract)
    except (ModelContractMismatch, OSError, ValueError):
        print("FAIL: selected model profile did not satisfy the compatibility contract")
        raise SystemExit(1) from None

    print(f"profile={result.contract.model_profile.value}")
    for work_class in WorkClass:
        accepted = any(probe.accepted and probe.work_class is work_class for probe in result.probes)
        print(f"{work_class.value}={'PASS' if accepted else 'FAIL'}")
    print("automatic_tools=PASS")
    print("forced_first_tools=PASS")
    print("PASS")


if __name__ == "__main__":
    main()
