from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


def camel_case(value: str) -> str:
    words = value.split("_")
    return words[0] + "".join(word.title() for word in words[1:])


class ProfileModel(BaseModel):
    model_config = ConfigDict(alias_generator=camel_case, populate_by_name=True, extra="forbid", frozen=True)


class WorkClass(StrEnum):
    CLARIFICATION = "clarification"
    ANALYSIS = "analysis"
    ARTIFACT = "artifact"
    VALIDATION = "validation"
    FORMATTING_REPAIR = "formatting_repair"


class ThinkingMode(StrEnum):
    ADAPTIVE = "adaptive"
    DISABLED = "disabled"


class ReasoningMode(StrEnum):
    STANDARD = "standard"


class ModelProfileId(StrEnum):
    CLAUDE_OPUS_4_8_XHIGH_V1 = "claude-opus-4-8-xhigh-v1"
    GPT_5_6_TERRA_MEDIUM_V1 = "gpt-5.6-terra-medium-v1"


class WorkProfile(ProfileModel):
    effort: Literal["medium", "high", "xhigh"]
    thinking: ThinkingMode | None = None
    reasoning_mode: ReasoningMode | None = None
    max_output_tokens: int = Field(ge=1, le=64000)

    @model_validator(mode="after")
    def require_one_reasoning_shape(self) -> WorkProfile:
        if (self.thinking is None) == (self.reasoning_mode is None):
            raise ValueError("exactly one provider reasoning shape is required")
        return self


OUTPUT_BUDGETS = {
    WorkClass.CLARIFICATION: 8000,
    WorkClass.ANALYSIS: 64000,
    WorkClass.ARTIFACT: 64000,
    WorkClass.VALIDATION: 32000,
    WorkClass.FORMATTING_REPAIR: 8000,
}


OPUS_WORK_PROFILES: dict[WorkClass, WorkProfile] = {
    WorkClass.CLARIFICATION: WorkProfile(effort="high", thinking=ThinkingMode.ADAPTIVE, max_output_tokens=8000),
    WorkClass.ANALYSIS: WorkProfile(effort="xhigh", thinking=ThinkingMode.ADAPTIVE, max_output_tokens=64000),
    WorkClass.ARTIFACT: WorkProfile(effort="xhigh", thinking=ThinkingMode.ADAPTIVE, max_output_tokens=64000),
    WorkClass.VALIDATION: WorkProfile(effort="high", thinking=ThinkingMode.ADAPTIVE, max_output_tokens=32000),
    WorkClass.FORMATTING_REPAIR: WorkProfile(effort="medium", thinking=ThinkingMode.DISABLED, max_output_tokens=8000),
}


TERRA_WORK_PROFILES: dict[WorkClass, WorkProfile] = {
    work_class: WorkProfile(
        effort="medium",
        reasoning_mode=ReasoningMode.STANDARD,
        max_output_tokens=max_output_tokens,
    )
    for work_class, max_output_tokens in OUTPUT_BUDGETS.items()
}


class ModelRuntimeProfile(ProfileModel):
    expected_base_model: Literal["claude-opus-4-8", "gpt-5.6-terra"]
    expected_snapshot: str | None = None
    prompt_version: Literal["claude-opus-4-8-v1", "gpt-5.6-terra-v1"]
    prompt_file: Literal["claude-opus-4-8-v1.md", "gpt-5.6-terra-v1.md"]
    allowed_hosting: tuple[Literal["azure", "anthropic"], ...]
    work_profiles: dict[WorkClass, WorkProfile]


MODEL_PROFILES: dict[ModelProfileId, ModelRuntimeProfile] = {
    ModelProfileId.CLAUDE_OPUS_4_8_XHIGH_V1: ModelRuntimeProfile(
        expected_base_model="claude-opus-4-8",
        prompt_version="claude-opus-4-8-v1",
        prompt_file="claude-opus-4-8-v1.md",
        allowed_hosting=("azure", "anthropic"),
        work_profiles=OPUS_WORK_PROFILES,
    ),
    ModelProfileId.GPT_5_6_TERRA_MEDIUM_V1: ModelRuntimeProfile(
        expected_base_model="gpt-5.6-terra",
        expected_snapshot="2026-07-09",
        prompt_version="gpt-5.6-terra-v1",
        prompt_file="gpt-5.6-terra-v1.md",
        allowed_hosting=("azure",),
        work_profiles=TERRA_WORK_PROFILES,
    ),
}


class VerifiedProfile(ProfileModel):
    request_options: dict[str, Any]
    response_evidence: tuple[str, ...]


class ModelContract(ProfileModel):
    schema_version: Literal["1.0"] = "1.0"
    deployment: str
    model_profile: ModelProfileId
    base_model: Literal["claude-opus-4-8", "gpt-5.6-terra"]
    base_model_snapshot: str | None = None
    hosting: Literal["azure", "anthropic"]
    wire_shape: Literal["responses_reasoning", "responses_extra_body_claude"]
    prompt_version: Literal["claude-opus-4-8-v1", "gpt-5.6-terra-v1"]
    prompt_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    request_options_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    verified_profiles: dict[WorkClass, VerifiedProfile]
    verified_at: str
    agent_framework_commit: Literal["ad26cfe8c7cb4d75a701eed13f6b5021cf1ad3ed"]

    def options_for(self, work_class: WorkClass) -> dict[str, Any]:
        if set(self.verified_profiles) != set(WorkClass):
            raise ValueError("model contract does not cover every work class")
        return dict(self.verified_profiles[work_class].request_options)
