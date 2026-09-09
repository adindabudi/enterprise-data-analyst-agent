from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast


@dataclass(frozen=True)
class InteractiveModelConfig:
    deployment: str
    instructions: str
    options: dict[str, Any]


def load_interactive_model_config(
    *,
    contract_path: Path,
    prompt_path: Path,
    expected_deployment: str,
) -> InteractiveModelConfig:
    prompt_bytes = prompt_path.read_bytes()
    if not prompt_bytes or len(prompt_bytes) > 16_384:
        raise ValueError("interactive prompt must contain 1-16384 bytes")
    prompt = prompt_bytes.decode("utf-8", errors="strict")
    contract_value = json.loads(contract_path.read_text(encoding="utf-8"))
    if not isinstance(contract_value, dict):
        raise ValueError("interactive model contract is malformed")
    contract = cast(dict[str, object], contract_value)
    if (
        contract.get("schemaVersion") != "1.0"
        or contract.get("deployment") != expected_deployment
        or contract.get("modelProfile") != "gpt-5.6-terra-medium-v1"
        or contract.get("baseModel") != "gpt-5.6-terra"
        or contract.get("baseModelSnapshot") != "2026-07-09"
        or contract.get("hosting") != "azure"
        or contract.get("promptVersion") != "gpt-5.6-terra-v1"
    ):
        raise ValueError("interactive model contract identity does not match")
    prompt_sha256 = contract.get("promptSha256")
    actual_prompt_sha256 = hashlib.sha256(prompt_bytes).hexdigest()
    if not isinstance(prompt_sha256, str) or not secrets.compare_digest(prompt_sha256, actual_prompt_sha256):
        raise ValueError("interactive prompt hash does not match model contract")
    profiles = contract.get("verifiedProfiles")
    if not isinstance(profiles, dict):
        raise ValueError("interactive model contract profiles are malformed")
    clarification = cast(dict[str, object], profiles).get("clarification")
    if not isinstance(clarification, dict):
        raise ValueError("interactive clarification profile is unavailable")
    options = cast(dict[str, object], clarification).get("requestOptions")
    if not isinstance(options, dict):
        raise ValueError("interactive clarification options are malformed")
    normalized = cast(dict[str, Any], json.loads(json.dumps(options)))
    if normalized != {
        "reasoning": {"mode": "standard", "effort": "medium"},
        "max_tokens": 8000,
        "store": False,
    }:
        raise ValueError("interactive clarification options are not approved")
    return InteractiveModelConfig(
        deployment=expected_deployment,
        instructions=prompt,
        options=normalized,
    )
