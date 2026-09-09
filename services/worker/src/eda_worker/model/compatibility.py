from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, Literal, Protocol, cast

from agent_framework import (
    ChatResponse,
    ChatResponseUpdate,
    Message,
    ResponseStream,
    SupportsChatGetResponse,
    UsageDetails,
)
from eda_worker.agent.prompt_loader import LoadedPrompt, load_prompt
from eda_worker.tools.capabilities import create_capability_tools
from eda_worker.tools.contracts import (
    CapabilityResult,
    CapabilityStatus,
    ExecuteSandboxOperation,
    InspectArtifactOperation,
    PublishArtifactOperation,
    ValidateArtifactOperation,
)
from pydantic import BaseModel, ConfigDict

from .profiles import (
    MODEL_PROFILES,
    ModelContract,
    ModelProfileId,
    ModelRuntimeProfile,
    ThinkingMode,
    VerifiedProfile,
    WorkClass,
)

AGENT_FRAMEWORK_COMMIT = "ad26cfe8c7cb4d75a701eed13f6b5021cf1ad3ed"
WireShape = Literal["responses_reasoning", "responses_extra_body_claude"]
Hosting = Literal["azure", "anthropic"]


class ModelContractMismatch(ValueError):
    pass


class CompatibilityModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Candidate(CompatibilityModel):
    wire_shape: WireShape
    options: dict[str, Any]


class ProbeResult(CompatibilityModel):
    deployment: str
    model_profile: ModelProfileId
    work_class: WorkClass
    candidate: Candidate
    accepted: bool
    served_model: str
    served_snapshot: str | None = None
    hosting: Hosting
    accepted_request_fields: tuple[str, ...] = ()
    usage_fields: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    failure: str | None = None


class ToolProbeResult(CompatibilityModel):
    mode: Literal["automatic", "forced_first"]
    accepted: bool
    tool_calls: tuple[str, ...]
    evidence: tuple[str, ...] = ()
    failure: str | None = None


class RuntimeSettings(Protocol):
    foundry_model_deployment: str
    eda_model_profile: str | ModelProfileId
    foundry_hosting: str


class ProbeClient(Protocol):
    def get_response(
        self,
        messages: Sequence[Message],
        *,
        stream: Literal[True],
        options: dict[str, Any],
    ) -> ResponseStream[ChatResponseUpdate, ChatResponse[Any]]: ...


class CompatibilityGateway:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def is_cancelled(self, task_id: str) -> bool:
        del task_id
        return False

    async def inspect(self, task_id: str, operation: InspectArtifactOperation) -> CapabilityResult:
        del task_id, operation
        self.calls.append("inspect_artifact")
        return CapabilityResult(status=CapabilityStatus.OK, summary="Synthetic artifact metadata is valid.")

    async def execute(self, task_id: str, operation: ExecuteSandboxOperation) -> CapabilityResult:
        del task_id, operation
        self.calls.append("execute_in_sandbox")
        return CapabilityResult(status=CapabilityStatus.OK, summary="Synthetic sandbox execution completed.")

    async def validate(self, task_id: str, operation: ValidateArtifactOperation) -> CapabilityResult:
        del task_id, operation
        self.calls.append("validate_artifact")
        return CapabilityResult(status=CapabilityStatus.OK, summary="Synthetic artifact validation passed.")

    async def publish(self, task_id: str, operation: PublishArtifactOperation) -> CapabilityResult:
        del task_id, operation
        self.calls.append("publish_artifact")
        return CapabilityResult(
            status=CapabilityStatus.OK,
            summary="Synthetic publication was recorded without an external effect.",
        )


def profile_candidates(profile_id: ModelProfileId, work_class: WorkClass) -> list[Candidate]:
    profile = MODEL_PROFILES[profile_id]
    work_profile = profile.work_profiles[work_class]
    base_options: dict[str, Any] = {
        "max_tokens": work_profile.max_output_tokens,
        "store": False,
    }
    if profile_id is ModelProfileId.GPT_5_6_TERRA_MEDIUM_V1:
        if work_profile.reasoning_mode is None:
            raise ModelContractMismatch("Terra work profile is missing its reasoning mode")
        return [
            Candidate(
                wire_shape="responses_reasoning",
                options={
                    "reasoning": {
                        "mode": work_profile.reasoning_mode.value,
                        "effort": work_profile.effort,
                    },
                    **base_options,
                },
            )
        ]
    if profile_id is ModelProfileId.CLAUDE_OPUS_4_8_XHIGH_V1:
        if work_profile.thinking is ThinkingMode.DISABLED:
            return [
                Candidate(
                    wire_shape="responses_extra_body_claude",
                    options={
                        "extra_body": {"output_config": {"effort": work_profile.effort}},
                        **base_options,
                    },
                )
            ]
        return [
            Candidate(
                wire_shape="responses_reasoning",
                options={"reasoning": {"effort": work_profile.effort}, **base_options},
            ),
            Candidate(
                wire_shape="responses_extra_body_claude",
                options={
                    "extra_body": {
                        "thinking": {"type": "adaptive", "display": "omitted"},
                        "output_config": {"effort": work_profile.effort},
                    },
                    **base_options,
                },
            ),
        ]
    raise ModelContractMismatch(f"unsupported model profile: {profile_id}")


async def run_probe(
    client: ProbeClient,
    work_class: WorkClass,
    candidate: Candidate,
    *,
    deployment: str,
    model_profile: ModelProfileId,
    prompt: LoadedPrompt,
    hosting: Hosting,
    retry_delay: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> ProbeResult:
    delays = (0.5, 1.0)
    for attempt in range(3):
        try:
            return await _run_probe_once(
                client,
                work_class,
                candidate,
                deployment=deployment,
                model_profile=model_profile,
                prompt=prompt,
                hosting=hosting,
            )
        except Exception as error:
            status_code = _exception_status_code(error)
            transient = status_code == 429 or (status_code is not None and status_code >= 500)
            if not transient:
                return _rejected_probe(
                    work_class,
                    candidate,
                    deployment=deployment,
                    model_profile=model_profile,
                    hosting=hosting,
                    failure=f"http_{status_code}" if status_code is not None else "probe_error",
                )
            if attempt == 2:
                return _rejected_probe(
                    work_class,
                    candidate,
                    deployment=deployment,
                    model_profile=model_profile,
                    hosting=hosting,
                    failure="transient_exhausted",
                )
            await retry_delay(delays[attempt])
    raise RuntimeError("unreachable probe retry state")


async def run_tool_probe(
    client: SupportsChatGetResponse[Any],
    candidate: Candidate,
    *,
    prompt: LoadedPrompt,
    forced_first: bool,
) -> ToolProbeResult:
    gateway = CompatibilityGateway()
    options = dict(candidate.options)
    options["tools"] = create_capability_tools(gateway)
    options["tool_choice"] = (
        {"mode": "required", "required_function_name": "inspect_artifact"} if forced_first else {"mode": "auto"}
    )
    artifact = {
        "artifact_id": "input-12345678",
        "version": 1,
        "kind": "input",
        "sha256": "a" * 64,
    }
    response = await client.get_response(
        [
            Message(role="system", contents=[prompt.text]),
            Message(
                role="user",
                contents=[
                    "Use exactly two tools in order. First call inspect_artifact with view=metadata for "
                    f"{json.dumps(artifact, sort_keys=True)}. Then call validate_artifact with profile=core_html "
                    "for the same artifact. After both results, reply with exactly: tool route complete"
                ],
            ),
        ],
        stream=False,
        options=options,
        function_invocation_kwargs={"task_id": "task_compatibility_probe"},
    )
    expected_calls = ("inspect_artifact", "validate_artifact")
    tool_calls = tuple(gateway.calls)
    stateless = response.conversation_id is None
    accepted = tool_calls == expected_calls and stateless and response.text.strip() == "tool route complete"
    mode: Literal["automatic", "forced_first"] = "forced_first" if forced_first else "automatic"
    evidence = ["stateless"] if stateless else []
    if tool_calls == expected_calls:
        evidence.append("forced_first_reset" if forced_first else "automatic_two_tool_route")
    return ToolProbeResult(
        mode=mode,
        accepted=accepted,
        tool_calls=tool_calls,
        evidence=tuple(sorted(evidence)),
        failure=None if accepted else "tool_route_mismatch",
    )


async def _run_probe_once(
    client: ProbeClient,
    work_class: WorkClass,
    candidate: Candidate,
    *,
    deployment: str,
    model_profile: ModelProfileId,
    prompt: LoadedPrompt,
    hosting: Hosting,
) -> ProbeResult:
    messages = [
        Message(role="system", contents=[prompt.text]),
        Message(
            role="user",
            contents=[f"Compatibility probe for {work_class.value}. Reply with exactly: phase complete"],
        ),
    ]
    stream = client.get_response(messages, stream=True, options=dict(candidate.options))
    streamed_text = False
    async for update in stream:
        if update.text:
            streamed_text = True
    response = await stream.get_final_response()

    usage = response.usage_details or UsageDetails()
    usage_fields = tuple(
        field
        for field, source in (
            ("input_tokens", "input_token_count"),
            ("output_tokens", "output_token_count"),
            ("reasoning_tokens", "reasoning_output_token_count"),
        )
        if isinstance(usage.get(source), int)
    )
    status = _metadata_value(response, "status", "response.status")
    snapshot = _metadata_value(
        response,
        "model_snapshot",
        "model_version",
        "snapshot",
        "response.model_snapshot",
        "response.model_version",
    )
    profile = MODEL_PROFILES[model_profile]
    served_model = response.model or ""
    expected_snapshot = profile.expected_snapshot
    if (
        not isinstance(snapshot, str)
        and expected_snapshot is not None
        and served_model == f"{profile.expected_base_model}-{expected_snapshot}"
    ):
        served_model = profile.expected_base_model
        snapshot = expected_snapshot
    reasoning_mode = _metadata_value(
        response,
        "reasoning_mode",
        "reasoning.mode",
        "response.reasoning.mode",
    )
    reasoning_effort = _metadata_value(
        response,
        "reasoning_effort",
        "reasoning.effort",
        "response.reasoning.effort",
    )
    evidence: list[str] = []
    if streamed_text:
        evidence.append("streamed_text")
    if status == "completed":
        evidence.append("completed")
    if response.conversation_id is None:
        evidence.append("stateless")
    if reasoning_mode == "standard":
        evidence.append("standard_mode")
    if isinstance(reasoning_effort, str):
        evidence.append(f"effort:{reasoning_effort}")

    accepted_fields: list[str] = []
    max_tokens = candidate.options.get("max_tokens")
    output_tokens = usage.get("output_token_count")
    if isinstance(max_tokens, int) and isinstance(output_tokens, int) and output_tokens <= max_tokens:
        accepted_fields.append("max_tokens")
    if candidate.options.get("store") is False and response.conversation_id is None:
        accepted_fields.append("store")
    reasoning = candidate.options.get("reasoning")
    if isinstance(reasoning, Mapping):
        reasoning_values = cast(Mapping[object, object], reasoning)
        expected_mode = reasoning_values.get("mode")
        expected_effort = reasoning_values.get("effort")
        if isinstance(expected_mode, str) and reasoning_mode == expected_mode:
            accepted_fields.append("reasoning.mode")
        if isinstance(expected_effort, str) and reasoning_effort == expected_effort:
            accepted_fields.append("reasoning.effort")

    required_usage = {"input_tokens", "output_tokens"}
    if profile.expected_base_model == "gpt-5.6-terra":
        required_usage.add("reasoning_tokens")
    accepted = (
        streamed_text
        and status == "completed"
        and response.conversation_id is None
        and bool(served_model)
        and isinstance(snapshot, str)
        and required_usage <= set(usage_fields)
        and _required_request_fields(candidate) <= set(accepted_fields)
    )
    return ProbeResult(
        deployment=deployment,
        model_profile=model_profile,
        work_class=work_class,
        candidate=candidate,
        accepted=accepted,
        served_model=served_model,
        served_snapshot=snapshot if isinstance(snapshot, str) else None,
        hosting=hosting,
        accepted_request_fields=tuple(sorted(accepted_fields)),
        usage_fields=usage_fields,
        evidence=tuple(sorted(evidence)),
        failure=None if accepted else "missing_evidence",
    )


def select_contract(
    results: Sequence[ProbeResult],
    loaded_prompt: LoadedPrompt,
    runtime_profile: ModelRuntimeProfile,
    *,
    verified_at: str | None = None,
) -> ModelContract:
    if not results:
        raise ModelContractMismatch("model compatibility results are empty")
    profile_ids = {result.model_profile for result in results}
    if profile_ids != {loaded_prompt.profile_id}:
        raise ModelContractMismatch("probe profile does not match the loaded prompt")
    profile_id = loaded_prompt.profile_id
    if runtime_profile != MODEL_PROFILES[profile_id]:
        raise ModelContractMismatch("runtime profile does not match the selected profile")

    accepted_by_work_class: dict[WorkClass, list[ProbeResult]] = {
        work_class: [result for result in results if result.work_class is work_class and result.accepted]
        for work_class in WorkClass
    }
    if any(not accepted_by_work_class[work_class] for work_class in WorkClass):
        raise ModelContractMismatch("one or more work classes have no accepted candidate")
    shape_sets: list[set[WireShape]] = [
        {result.candidate.wire_shape for result in accepted_by_work_class[work_class]} for work_class in WorkClass
    ]
    common_shapes: set[WireShape] = set(shape_sets[0])
    for shapes in shape_sets[1:]:
        common_shapes.intersection_update(shapes)
    if len(common_shapes) != 1:
        raise ModelContractMismatch("exactly one wire shape must pass every work class")
    wire_shape = common_shapes.pop()

    selected: dict[WorkClass, ProbeResult] = {}
    for work_class in WorkClass:
        matching = [
            result for result in accepted_by_work_class[work_class] if result.candidate.wire_shape == wire_shape
        ]
        if len(matching) != 1:
            raise ModelContractMismatch(f"work class {work_class.value} has ambiguous accepted candidates")
        result = matching[0]
        if result.candidate not in profile_candidates(profile_id, work_class):
            raise ModelContractMismatch(f"work class {work_class.value} accepted an unconfigured candidate")
        _validate_probe_evidence(result, runtime_profile)
        selected[work_class] = result

    deployments = {result.deployment for result in selected.values()}
    hostings: set[Hosting] = {result.hosting for result in selected.values()}
    models = {result.served_model for result in selected.values()}
    snapshots = {result.served_snapshot for result in selected.values()}
    if len(deployments) != 1:
        raise ModelContractMismatch("selected candidates span multiple deployments")
    if len(hostings) != 1 or not hostings <= set(runtime_profile.allowed_hosting):
        raise ModelContractMismatch("selected candidates have incompatible hosting")
    if models != {runtime_profile.expected_base_model}:
        raise ModelContractMismatch("served model does not match the runtime profile")
    if snapshots != {runtime_profile.expected_snapshot}:
        raise ModelContractMismatch("served snapshot does not match the runtime profile")
    if loaded_prompt.version != runtime_profile.prompt_version:
        raise ModelContractMismatch("loaded prompt version does not match the runtime profile")

    verified_profiles = {
        work_class: VerifiedProfile(
            request_options=selected[work_class].candidate.options,
            response_evidence=_response_evidence(selected[work_class]),
        )
        for work_class in WorkClass
    }
    return ModelContract(
        deployment=deployments.pop(),
        model_profile=profile_id,
        base_model=runtime_profile.expected_base_model,
        base_model_snapshot=runtime_profile.expected_snapshot,
        hosting=hostings.pop(),
        wire_shape=wire_shape,
        prompt_version=runtime_profile.prompt_version,
        prompt_sha256=loaded_prompt.sha256,
        request_options_sha256=request_options_sha256(verified_profiles),
        verified_profiles=verified_profiles,
        verified_at=verified_at or datetime.now(UTC).isoformat(),
        agent_framework_commit=AGENT_FRAMEWORK_COMMIT,
    )


def validate_contract(contract: ModelContract, settings: RuntimeSettings) -> None:
    try:
        selected_profile = ModelProfileId(settings.eda_model_profile)
    except ValueError as error:
        raise ModelContractMismatch("runtime model profile is unsupported") from error
    profile = MODEL_PROFILES[selected_profile]
    if contract.deployment != settings.foundry_model_deployment:
        raise ModelContractMismatch("model contract deployment does not match runtime deployment")
    if contract.model_profile is not selected_profile:
        raise ModelContractMismatch("model contract profile does not match runtime profile")
    if contract.hosting != settings.foundry_hosting or contract.hosting not in profile.allowed_hosting:
        raise ModelContractMismatch("model contract hosting does not match runtime hosting")
    if contract.base_model != profile.expected_base_model or contract.base_model_snapshot != profile.expected_snapshot:
        raise ModelContractMismatch("model contract identity does not match runtime profile")
    prompt = load_prompt(selected_profile)
    if contract.prompt_version != prompt.version or contract.prompt_sha256 != prompt.sha256:
        raise ModelContractMismatch("model contract prompt does not match the packaged prompt")
    if set(contract.verified_profiles) != set(WorkClass):
        raise ModelContractMismatch("model contract does not cover every work class")
    for work_class in WorkClass:
        candidate = Candidate(wire_shape=contract.wire_shape, options=contract.options_for(work_class))
        if candidate not in profile_candidates(selected_profile, work_class):
            raise ModelContractMismatch(f"model contract options do not match {work_class.value}")
    if contract.request_options_sha256 != request_options_sha256(contract.verified_profiles):
        raise ModelContractMismatch("model contract request options hash is invalid")


def request_options_sha256(verified_profiles: dict[WorkClass, VerifiedProfile]) -> str:
    canonical = {
        work_class.value: verified_profiles[work_class].request_options
        for work_class in sorted(verified_profiles, key=lambda value: value.value)
    }
    payload = json.dumps(canonical, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()
    return sha256(payload).hexdigest()


def _exception_status_code(error: Exception) -> int | None:
    status_code = cast(object, getattr(error, "status_code", None))
    if isinstance(status_code, int):
        return status_code
    response = cast(object, getattr(error, "response", None))
    response_status = cast(object, getattr(response, "status_code", None))
    return response_status if isinstance(response_status, int) else None


def _rejected_probe(
    work_class: WorkClass,
    candidate: Candidate,
    *,
    deployment: str,
    model_profile: ModelProfileId,
    hosting: Hosting,
    failure: str,
) -> ProbeResult:
    return ProbeResult(
        deployment=deployment,
        model_profile=model_profile,
        work_class=work_class,
        candidate=candidate,
        accepted=False,
        served_model="",
        hosting=hosting,
        failure=failure,
    )


def _metadata_value(response: ChatResponse[Any], *keys: str) -> object:
    sources: list[object] = [response.additional_properties, response.raw_representation, response.value]
    for source in sources:
        for key in keys:
            value = _read_value(source, key)
            if value is not None:
                return value
    return None


def _read_value(source: object, path: str) -> object:
    if isinstance(source, Sequence) and not isinstance(source, (str, bytes, bytearray)):
        for item in reversed(cast(Sequence[object], source)):
            value = _read_value(item, path)
            if value is not None:
                return value
        return None
    value = source
    for segment in path.split("."):
        if isinstance(value, Mapping):
            value = cast(Mapping[object, object], value).get(segment)
        else:
            value = cast(object, getattr(value, segment, None))
        if value is None:
            return None
    return value


def _required_request_fields(candidate: Candidate) -> set[str]:
    fields = {key for key in ("max_tokens", "store") if key in candidate.options}
    reasoning = candidate.options.get("reasoning")
    if isinstance(reasoning, Mapping):
        reasoning_values = cast(Mapping[object, object], reasoning)
        fields.update(f"reasoning.{key}" for key in ("mode", "effort") if key in reasoning_values)
    return fields


def _validate_probe_evidence(result: ProbeResult, runtime_profile: ModelRuntimeProfile) -> None:
    evidence = set(result.evidence)
    usage = set(result.usage_fields)
    if not {"completed", "streamed_text"} <= evidence:
        raise ModelContractMismatch(f"work class {result.work_class.value} is missing completion or stream evidence")
    if not {"input_tokens", "output_tokens"} <= usage:
        raise ModelContractMismatch(f"work class {result.work_class.value} is missing token usage evidence")
    if not result.accepted_request_fields:
        raise ModelContractMismatch(f"work class {result.work_class.value} is missing request-field evidence")
    if runtime_profile.expected_base_model == "gpt-5.6-terra":
        if "reasoning_tokens" not in usage or "standard_mode" not in evidence:
            raise ModelContractMismatch(f"work class {result.work_class.value} is missing Terra reasoning evidence")


def _response_evidence(result: ProbeResult) -> tuple[str, ...]:
    snapshot = f"@{result.served_snapshot}" if result.served_snapshot is not None else ""
    values = {
        f"served:{result.served_model}{snapshot}",
        f"hosting:{result.hosting}",
        *(f"request:{field}" for field in result.accepted_request_fields),
        *(f"usage:{field}" for field in result.usage_fields),
        *(f"marker:{marker}" for marker in result.evidence),
    }
    return tuple(sorted(values))
