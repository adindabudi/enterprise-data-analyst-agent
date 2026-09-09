from types import SimpleNamespace

import pytest
from eda_worker.agent.prompt_loader import load_prompt
from eda_worker.model.profiles import (
    MODEL_PROFILES,
    ModelContract,
    ModelProfileId,
    VerifiedProfile,
)
from eda_worker.model.startup import load_startup_model_state, request_options_sha256
from eda_worker.model.tokenizer import Calibration


def model_contract(profile_id: ModelProfileId) -> ModelContract:
    profile = MODEL_PROFILES[profile_id]
    prompt = load_prompt(profile_id)
    verified_profiles = {
        work_class: VerifiedProfile(
            request_options={
                "reasoning": {"mode": "standard", "effort": "medium"},
                "max_tokens": work_profile.max_output_tokens,
                "store": False,
            },
            response_evidence=("served:gpt-5.6-terra@2026-07-09",),
        )
        for work_class, work_profile in profile.work_profiles.items()
    }
    return ModelContract(
        deployment="analysis-terra",
        model_profile=profile_id,
        base_model=profile.expected_base_model,
        base_model_snapshot=profile.expected_snapshot,
        hosting="azure",
        wire_shape="responses_reasoning",
        prompt_version=profile.prompt_version,
        prompt_sha256=prompt.sha256,
        request_options_sha256=request_options_sha256(verified_profiles),
        verified_profiles=verified_profiles,
        verified_at="2026-07-26T00:00:00Z",
        agent_framework_commit="ad26cfe8c7cb4d75a701eed13f6b5021cf1ad3ed",
    )


def write_startup_artifacts(tmp_path, *, prompt_sha256: str | None = None):
    profile_id = ModelProfileId.GPT_5_6_TERRA_MEDIUM_V1
    contract = model_contract(profile_id)
    if prompt_sha256 is not None:
        contract = contract.model_copy(update={"prompt_sha256": prompt_sha256})
    contract_path = tmp_path / "model-contract.json"
    contract_path.write_text(contract.model_dump_json(by_alias=True))
    calibration = Calibration(
        deployment="analysis-terra",
        model_profile=profile_id,
        base_model="gpt-5.6-terra",
        count_source="responses_usage_input_tokens",
        chars_per_token=3.5,
        safety_margin=1.25,
        samples=20,
    )
    calibration_path = tmp_path / "tokenizer-calibration.json"
    calibration_path.write_text(calibration.model_dump_json(by_alias=True))
    settings = SimpleNamespace(
        eda_model_profile=profile_id,
        foundry_model_deployment="analysis-terra",
        foundry_hosting="azure",
        model_contract_path=str(contract_path),
        tokenizer_calibration_path=str(calibration_path),
    )
    return settings


def test_startup_model_state_validates_profile_prompt_options_and_tokenizer(tmp_path) -> None:
    state = load_startup_model_state(write_startup_artifacts(tmp_path))

    assert state.contract.model_profile is ModelProfileId.GPT_5_6_TERRA_MEDIUM_V1
    assert state.prompt.version == "gpt-5.6-terra-v1"
    assert state.calibration.count_source == "responses_usage_input_tokens"
    assert state.tokenizer.calibration.deployment == "analysis-terra"


def test_startup_model_state_rejects_prompt_hash_mismatch(tmp_path) -> None:
    with pytest.raises(ValueError, match="prompt hash"):
        load_startup_model_state(write_startup_artifacts(tmp_path, prompt_sha256="0" * 64))
