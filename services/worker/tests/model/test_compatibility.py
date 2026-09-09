from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Literal

import pytest
from agent_framework import (
    BaseChatClient,
    ChatMiddlewareLayer,
    ChatResponse,
    ChatResponseUpdate,
    Content,
    FunctionInvocationLayer,
    Message,
    ResponseStream,
    UsageDetails,
)
from eda_worker.agent.prompt_loader import load_prompt
from eda_worker.model.compatibility import (
    Candidate,
    ModelContractMismatch,
    ProbeResult,
    WireShape,
    profile_candidates,
    run_probe,
    run_tool_probe,
    select_contract,
    validate_contract,
)
from eda_worker.model.profiles import MODEL_PROFILES, ModelProfileId, WorkClass


@dataclass
class RuntimeSettings:
    foundry_model_deployment: str
    eda_model_profile: str | ModelProfileId
    foundry_hosting: str


class ProbeClient:
    def __init__(self, response: ChatResponse[Any]) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def get_response(
        self,
        messages: Sequence[Message],
        *,
        stream: Literal[True],
        options: dict[str, Any],
    ) -> ResponseStream[ChatResponseUpdate, ChatResponse[Any]]:
        self.calls.append({"messages": list(messages), "stream": stream, "options": options})

        async def updates() -> AsyncIterator[ChatResponseUpdate]:
            yield ChatResponseUpdate(contents=[Content.from_text("phase complete")], model=self.response.model)

        return ResponseStream(updates(), finalizer=lambda _: self.response)


class ProbeHttpError(RuntimeError):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


class SequencedProbeClient(ProbeClient):
    def __init__(self, response: ChatResponse[Any], failures: list[ProbeHttpError]) -> None:
        super().__init__(response)
        self.failures = failures
        self.attempts = 0

    def get_response(
        self,
        messages: Sequence[Message],
        *,
        stream: Literal[True],
        options: dict[str, Any],
    ) -> ResponseStream[ChatResponseUpdate, ChatResponse[Any]]:
        self.attempts += 1
        if self.failures:
            raise self.failures.pop(0)
        return super().get_response(messages, stream=stream, options=options)


class ToolRoutingClient(FunctionInvocationLayer, ChatMiddlewareLayer, BaseChatClient):
    def __init__(self) -> None:
        super().__init__()
        self.inner_options: list[dict[str, Any]] = []

    def _inner_get_response(
        self,
        *,
        messages: Sequence[Message],
        stream: bool,
        options: Mapping[str, Any],
        **kwargs: Any,
    ) -> Any:
        del messages, kwargs
        if stream:
            raise ValueError("tool routing test client is nonstreaming")
        self.inner_options.append(dict(options))

        async def response() -> ChatResponse[Any]:
            artifact = {
                "artifact_id": "input-12345678",
                "version": 1,
                "kind": "input",
                "sha256": "a" * 64,
            }
            if len(self.inner_options) == 1:
                return ChatResponse(
                    messages=Message(
                        role="assistant",
                        contents=[
                            Content.from_function_call(
                                "call_inspect",
                                "inspect_artifact",
                                arguments={"artifact": artifact, "view": "metadata"},
                            )
                        ],
                    ),
                    conversation_id=None,
                    finish_reason="tool_calls",
                )
            if len(self.inner_options) == 2:
                return ChatResponse(
                    messages=Message(
                        role="assistant",
                        contents=[
                            Content.from_function_call(
                                "call_validate",
                                "validate_artifact",
                                arguments={"artifact": artifact, "profile": "core_html"},
                            )
                        ],
                    ),
                    conversation_id=None,
                    finish_reason="tool_calls",
                )
            return ChatResponse(
                messages=Message(role="assistant", contents=["tool route complete"]),
                conversation_id=None,
                finish_reason="stop",
            )

        return response()


def test_opus_candidate_shapes_are_explicit_and_do_not_mix_protocols() -> None:
    candidates = profile_candidates(ModelProfileId.CLAUDE_OPUS_4_8_XHIGH_V1, WorkClass.ANALYSIS)

    assert candidates == [
        Candidate(
            wire_shape="responses_reasoning",
            options={"reasoning": {"effort": "xhigh"}, "max_tokens": 64_000, "store": False},
        ),
        Candidate(
            wire_shape="responses_extra_body_claude",
            options={
                "extra_body": {
                    "thinking": {"type": "adaptive", "display": "omitted"},
                    "output_config": {"effort": "xhigh"},
                },
                "max_tokens": 64_000,
                "store": False,
            },
        ),
    ]


def test_terra_candidate_is_exact_standard_medium_responses_shape() -> None:
    candidates = profile_candidates(ModelProfileId.GPT_5_6_TERRA_MEDIUM_V1, WorkClass.ANALYSIS)

    assert candidates == [
        Candidate(
            wire_shape="responses_reasoning",
            options={
                "reasoning": {"mode": "standard", "effort": "medium"},
                "max_tokens": 64_000,
                "store": False,
            },
        )
    ]


def _terra_results(*, wire_shape: WireShape = "responses_reasoning") -> list[ProbeResult]:
    return [
        ProbeResult(
            deployment="gpt-5.6-terra-medium-v1",
            model_profile=ModelProfileId.GPT_5_6_TERRA_MEDIUM_V1,
            work_class=work_class,
            candidate=Candidate(
                wire_shape=wire_shape,
                options={
                    "reasoning": {"mode": "standard", "effort": "medium"},
                    "max_tokens": MODEL_PROFILES[ModelProfileId.GPT_5_6_TERRA_MEDIUM_V1]
                    .work_profiles[work_class]
                    .max_output_tokens,
                    "store": False,
                },
            ),
            accepted=True,
            served_model="gpt-5.6-terra",
            served_snapshot="2026-07-09",
            hosting="azure",
            accepted_request_fields=("max_tokens", "reasoning.effort", "reasoning.mode", "store"),
            usage_fields=("input_tokens", "output_tokens", "reasoning_tokens"),
            evidence=("completed", "streamed_text", "standard_mode"),
        )
        for work_class in WorkClass
    ]


def test_select_contract_requires_one_shape_for_every_work_class() -> None:
    prompt = load_prompt(ModelProfileId.GPT_5_6_TERRA_MEDIUM_V1)

    contract = select_contract(
        _terra_results(),
        prompt,
        MODEL_PROFILES[ModelProfileId.GPT_5_6_TERRA_MEDIUM_V1],
        verified_at="2026-07-26T00:00:00Z",
    )

    assert contract.model_profile is ModelProfileId.GPT_5_6_TERRA_MEDIUM_V1
    assert contract.wire_shape == "responses_reasoning"
    assert set(contract.verified_profiles) == set(WorkClass)
    assert (
        contract.options_for(WorkClass.ANALYSIS)
        == profile_candidates(ModelProfileId.GPT_5_6_TERRA_MEDIUM_V1, WorkClass.ANALYSIS)[0].options
    )


def test_select_contract_rejects_mixed_shapes() -> None:
    results = _terra_results()
    results[-1] = results[-1].model_copy(
        update={"candidate": results[-1].candidate.model_copy(update={"wire_shape": "responses_extra_body_claude"})}
    )

    with pytest.raises(ModelContractMismatch, match="wire shape"):
        select_contract(
            results,
            load_prompt(ModelProfileId.GPT_5_6_TERRA_MEDIUM_V1),
            MODEL_PROFILES[ModelProfileId.GPT_5_6_TERRA_MEDIUM_V1],
        )


def test_runtime_rejects_contract_for_other_deployment() -> None:
    contract = select_contract(
        _terra_results(),
        load_prompt(ModelProfileId.GPT_5_6_TERRA_MEDIUM_V1),
        MODEL_PROFILES[ModelProfileId.GPT_5_6_TERRA_MEDIUM_V1],
    )
    settings = RuntimeSettings(
        foundry_model_deployment="other",
        eda_model_profile=ModelProfileId.GPT_5_6_TERRA_MEDIUM_V1,
        foundry_hosting="azure",
    )

    with pytest.raises(ModelContractMismatch, match="deployment"):
        validate_contract(contract, settings)


@pytest.mark.asyncio
async def test_run_probe_accepts_complete_terra_stream_evidence() -> None:
    client = ProbeClient(
        ChatResponse(
            messages=Message(role="assistant", contents=["phase complete"]),
            model="gpt-5.6-terra",
            conversation_id=None,
            finish_reason="stop",
            usage_details=UsageDetails(
                input_token_count=100,
                output_token_count=20,
                reasoning_output_token_count=8,
            ),
            additional_properties={
                "status": "completed",
                "model_snapshot": "2026-07-09",
                "reasoning_mode": "standard",
                "reasoning_effort": "medium",
            },
        )
    )
    prompt = load_prompt(ModelProfileId.GPT_5_6_TERRA_MEDIUM_V1)
    candidate = profile_candidates(ModelProfileId.GPT_5_6_TERRA_MEDIUM_V1, WorkClass.ANALYSIS)[0]

    result = await run_probe(
        client,
        WorkClass.ANALYSIS,
        candidate,
        deployment="gpt-5.6-terra-medium-v1",
        model_profile=ModelProfileId.GPT_5_6_TERRA_MEDIUM_V1,
        prompt=prompt,
        hosting="azure",
    )

    assert result.accepted is True
    assert result.usage_fields == ("input_tokens", "output_tokens", "reasoning_tokens")
    assert set(result.evidence) >= {"completed", "stateless", "streamed_text", "standard_mode"}
    assert set(result.accepted_request_fields) == {"max_tokens", "reasoning.effort", "reasoning.mode", "store"}
    assert prompt.text not in result.model_dump_json()
    assert client.calls[0]["stream"] is True
    assert client.calls[0]["options"] == candidate.options


@pytest.mark.asyncio
async def test_run_probe_normalizes_exact_combined_terra_served_model_header() -> None:
    client = ProbeClient(
        ChatResponse(
            messages=Message(role="assistant", contents=["phase complete"]),
            model="gpt-5.6-terra-2026-07-09",
            conversation_id=None,
            finish_reason="stop",
            usage_details=UsageDetails(
                input_token_count=100,
                output_token_count=20,
                reasoning_output_token_count=8,
            ),
            additional_properties={
                "status": "completed",
                "reasoning_mode": "standard",
                "reasoning_effort": "medium",
            },
        )
    )

    result = await run_probe(
        client,
        WorkClass.ANALYSIS,
        profile_candidates(ModelProfileId.GPT_5_6_TERRA_MEDIUM_V1, WorkClass.ANALYSIS)[0],
        deployment="gpt-5.6-terra",
        model_profile=ModelProfileId.GPT_5_6_TERRA_MEDIUM_V1,
        prompt=load_prompt(ModelProfileId.GPT_5_6_TERRA_MEDIUM_V1),
        hosting="azure",
    )

    assert result.accepted is True
    assert result.served_model == "gpt-5.6-terra"
    assert result.served_snapshot == "2026-07-09"


@pytest.mark.asyncio
async def test_run_probe_rejects_2xx_when_terra_reasoning_evidence_is_missing() -> None:
    client = ProbeClient(
        ChatResponse(
            messages=Message(role="assistant", contents=["phase complete"]),
            model="gpt-5.6-terra",
            conversation_id=None,
            finish_reason="stop",
            usage_details=UsageDetails(input_token_count=100, output_token_count=20),
            additional_properties={"status": "completed", "model_snapshot": "2026-07-09"},
        )
    )

    result = await run_probe(
        client,
        WorkClass.ANALYSIS,
        profile_candidates(ModelProfileId.GPT_5_6_TERRA_MEDIUM_V1, WorkClass.ANALYSIS)[0],
        deployment="gpt-5.6-terra-medium-v1",
        model_profile=ModelProfileId.GPT_5_6_TERRA_MEDIUM_V1,
        prompt=load_prompt(ModelProfileId.GPT_5_6_TERRA_MEDIUM_V1),
        hosting="azure",
    )

    assert result.accepted is False
    assert result.failure == "missing_evidence"


@pytest.mark.asyncio
async def test_run_probe_reads_evidence_from_aggregated_raw_completed_event() -> None:
    client = ProbeClient(
        ChatResponse(
            messages=Message(role="assistant", contents=["phase complete"]),
            model="gpt-5.6-terra",
            conversation_id=None,
            finish_reason="stop",
            usage_details=UsageDetails(
                input_token_count=100,
                output_token_count=20,
                reasoning_output_token_count=8,
            ),
            raw_representation=[
                SimpleNamespace(
                    type="response.completed",
                    response=SimpleNamespace(
                        status="completed",
                        model_snapshot="2026-07-09",
                        reasoning=SimpleNamespace(mode="standard", effort="medium"),
                    ),
                )
            ],
        )
    )

    result = await run_probe(
        client,
        WorkClass.ANALYSIS,
        profile_candidates(ModelProfileId.GPT_5_6_TERRA_MEDIUM_V1, WorkClass.ANALYSIS)[0],
        deployment="gpt-5.6-terra-medium-v1",
        model_profile=ModelProfileId.GPT_5_6_TERRA_MEDIUM_V1,
        prompt=load_prompt(ModelProfileId.GPT_5_6_TERRA_MEDIUM_V1),
        hosting="azure",
    )

    assert result.accepted is True
    assert "response.completed" not in result.model_dump_json()


def _complete_terra_response() -> ChatResponse[Any]:
    return ChatResponse(
        messages=Message(role="assistant", contents=["phase complete"]),
        model="gpt-5.6-terra",
        conversation_id=None,
        finish_reason="stop",
        usage_details=UsageDetails(
            input_token_count=100,
            output_token_count=20,
            reasoning_output_token_count=8,
        ),
        additional_properties={
            "status": "completed",
            "model_snapshot": "2026-07-09",
            "reasoning_mode": "standard",
            "reasoning_effort": "medium",
        },
    )


@pytest.mark.asyncio
async def test_run_probe_does_not_retry_http_400() -> None:
    client = SequencedProbeClient(_complete_terra_response(), [ProbeHttpError(400)])

    result = await run_probe(
        client,
        WorkClass.ANALYSIS,
        profile_candidates(ModelProfileId.GPT_5_6_TERRA_MEDIUM_V1, WorkClass.ANALYSIS)[0],
        deployment="gpt-5.6-terra-medium-v1",
        model_profile=ModelProfileId.GPT_5_6_TERRA_MEDIUM_V1,
        prompt=load_prompt(ModelProfileId.GPT_5_6_TERRA_MEDIUM_V1),
        hosting="azure",
    )

    assert result.accepted is False
    assert result.failure == "http_400"
    assert client.attempts == 1


@pytest.mark.asyncio
async def test_run_probe_retries_transient_failures_at_most_three_attempts() -> None:
    client = SequencedProbeClient(
        _complete_terra_response(),
        [ProbeHttpError(429), ProbeHttpError(503)],
    )
    delays: list[float] = []

    async def record_delay(seconds: float) -> None:
        delays.append(seconds)

    result = await run_probe(
        client,
        WorkClass.ANALYSIS,
        profile_candidates(ModelProfileId.GPT_5_6_TERRA_MEDIUM_V1, WorkClass.ANALYSIS)[0],
        deployment="gpt-5.6-terra-medium-v1",
        model_profile=ModelProfileId.GPT_5_6_TERRA_MEDIUM_V1,
        prompt=load_prompt(ModelProfileId.GPT_5_6_TERRA_MEDIUM_V1),
        hosting="azure",
        retry_delay=record_delay,
    )

    assert result.accepted is True
    assert client.attempts == 3
    assert delays == [0.5, 1.0]


@pytest.mark.asyncio
async def test_automatic_tool_probe_uses_two_actual_core_tools() -> None:
    client = ToolRoutingClient()

    result = await run_tool_probe(
        client,
        profile_candidates(ModelProfileId.GPT_5_6_TERRA_MEDIUM_V1, WorkClass.ANALYSIS)[0],
        prompt=load_prompt(ModelProfileId.GPT_5_6_TERRA_MEDIUM_V1),
        forced_first=False,
    )

    assert result.accepted is True
    assert result.mode == "automatic"
    assert result.tool_calls == ("inspect_artifact", "validate_artifact")
    assert client.inner_options[0]["tool_choice"] == {"mode": "auto"}


@pytest.mark.asyncio
async def test_forced_tool_probe_resets_required_choice_after_first_iteration() -> None:
    client = ToolRoutingClient()

    result = await run_tool_probe(
        client,
        profile_candidates(ModelProfileId.GPT_5_6_TERRA_MEDIUM_V1, WorkClass.ANALYSIS)[0],
        prompt=load_prompt(ModelProfileId.GPT_5_6_TERRA_MEDIUM_V1),
        forced_first=True,
    )

    assert result.accepted is True
    assert result.mode == "forced_first"
    assert result.tool_calls == ("inspect_artifact", "validate_artifact")
    assert client.inner_options[0]["tool_choice"] == {
        "mode": "required",
        "required_function_name": "inspect_artifact",
    }
    assert client.inner_options[1]["tool_choice"] is None
