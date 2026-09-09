from importlib.metadata import version

import pytest
from eda_worker.model.profiles import MODEL_PROFILES, ModelContract, ModelProfileId, WorkClass
from pydantic import ValidationError


def test_foundry_package_is_exact() -> None:
    assert version("agent-framework-foundry") == "1.11.0"


@pytest.mark.parametrize("profile_id", list(ModelProfileId))
def test_every_model_profile_has_an_explicit_budget(profile_id: ModelProfileId) -> None:
    work_profiles = MODEL_PROFILES[profile_id].work_profiles
    assert set(work_profiles) == set(WorkClass)
    assert work_profiles[WorkClass.CLARIFICATION].max_output_tokens == 8000
    assert work_profiles[WorkClass.ANALYSIS].max_output_tokens == 64000
    assert work_profiles[WorkClass.ARTIFACT].max_output_tokens == 64000
    assert work_profiles[WorkClass.VALIDATION].max_output_tokens == 32000
    assert work_profiles[WorkClass.FORMATTING_REPAIR].max_output_tokens == 8000


def test_terra_v1_is_medium_standard_without_an_opus_thinking_shape() -> None:
    profile = MODEL_PROFILES[ModelProfileId.GPT_5_6_TERRA_MEDIUM_V1]

    assert profile.expected_base_model == "gpt-5.6-terra"
    assert profile.expected_snapshot == "2026-07-09"
    assert {item.effort for item in profile.work_profiles.values()} == {"medium"}
    assert {item.reasoning_mode for item in profile.work_profiles.values()} == {"standard"}
    assert all(item.thinking is None for item in profile.work_profiles.values())


def test_contract_rejects_unverified_or_fallback_shapes() -> None:
    with pytest.raises(ValidationError):
        ModelContract.model_validate(
            {
                "schemaVersion": "1.0",
                "deployment": "analysis-opus",
                "modelProfile": "claude-opus-4-8-xhigh-v1",
                "baseModel": "claude-opus-4-8",
                "wireShape": "unverified",
                "promptVersion": "claude-opus-4-8-v1",
                "promptSha256": "a" * 64,
                "requestOptionsSha256": "b" * 64,
                "verifiedProfiles": {},
            }
        )
