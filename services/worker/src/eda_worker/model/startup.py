from __future__ import annotations

import json
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from ..agent.prompt_loader import LoadedPrompt, load_prompt
from .profiles import MODEL_PROFILES, ModelContract, ModelProfileId, VerifiedProfile, WorkClass
from .tokenizer import CalibratedTokenizer, Calibration


@dataclass(frozen=True)
class StartupModelState:
    contract: ModelContract
    prompt: LoadedPrompt
    calibration: Calibration
    tokenizer: CalibratedTokenizer


def request_options_sha256(verified_profiles: Mapping[WorkClass, VerifiedProfile]) -> str:
    canonical = {
        work_class.value: verified_profiles[work_class].request_options
        for work_class in sorted(verified_profiles, key=lambda value: value.value)
    }
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return sha256(payload).hexdigest()


def load_startup_model_state(settings: Any) -> StartupModelState:
    profile_id = ModelProfileId(settings.eda_model_profile)
    profile = MODEL_PROFILES[profile_id]
    contract = ModelContract.model_validate_json(Path(settings.model_contract_path).read_text(encoding="utf-8"))
    calibration = Calibration.model_validate_json(Path(settings.tokenizer_calibration_path).read_text(encoding="utf-8"))
    prompt = load_prompt(profile_id)

    if contract.model_profile is not profile_id:
        raise ValueError("model contract profile does not match deployment intent")
    if contract.deployment != settings.foundry_model_deployment:
        raise ValueError("model contract deployment does not match configured deployment")
    if contract.base_model != profile.expected_base_model or contract.base_model_snapshot != profile.expected_snapshot:
        raise ValueError("model contract base model or snapshot does not match selected profile")
    if contract.hosting != settings.foundry_hosting or contract.hosting not in profile.allowed_hosting:
        raise ValueError("model contract hosting does not match selected profile")
    expected_wire_shape = (
        "responses_reasoning" if profile_id is ModelProfileId.GPT_5_6_TERRA_MEDIUM_V1 else "responses_extra_body_claude"
    )
    if contract.wire_shape != expected_wire_shape:
        raise ValueError("model contract wire shape does not match selected profile")
    if contract.prompt_version != prompt.version:
        raise ValueError("model contract prompt version does not match prompt asset")
    if not secrets.compare_digest(contract.prompt_sha256, prompt.sha256):
        raise ValueError("model contract prompt hash does not match prompt asset")
    for work_class in WorkClass:
        contract.options_for(work_class)
    options_hash = request_options_sha256(contract.verified_profiles)
    if not secrets.compare_digest(contract.request_options_sha256, options_hash):
        raise ValueError("model contract request options hash does not match verified profiles")

    expected_count_source = (
        "responses_usage_input_tokens" if profile_id is ModelProfileId.GPT_5_6_TERRA_MEDIUM_V1 else "anthropic_messages"
    )
    if calibration.deployment != contract.deployment:
        raise ValueError("tokenizer calibration deployment does not match model contract")
    if calibration.model_profile is not profile_id or calibration.base_model != contract.base_model:
        raise ValueError("tokenizer calibration model identity does not match model contract")
    if calibration.count_source != expected_count_source:
        raise ValueError("tokenizer calibration count source does not match selected profile")
    return StartupModelState(
        contract=contract,
        prompt=prompt,
        calibration=calibration,
        tokenizer=CalibratedTokenizer(calibration),
    )
