from __future__ import annotations

import os
import re
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path

import pytest
from eda_worker.agent.prompt_loader import load_prompt
from eda_worker.model.profiles import MODEL_PROFILES, ModelContract, ModelProfileId, WorkClass
from eda_worker.model.tokenizer import Calibration

pytestmark = pytest.mark.cloud
RUN_LIVE = os.environ.get("EDA_RUN_MODEL_COMPATIBILITY") == "1"


@pytest.mark.skipif(not RUN_LIVE, reason="set EDA_RUN_MODEL_COMPATIBILITY=1 to run the live model gate")
def test_live_model_contract_and_calibration_are_profile_bound(tmp_path: Path) -> None:
    project_endpoint = os.environ["EDA_FOUNDRY_PROJECT_ENDPOINT"]
    resource_endpoint = os.environ["EDA_FOUNDRY_RESOURCE_ENDPOINT"]
    deployment = os.environ["EDA_FOUNDRY_MODEL_DEPLOYMENT"]
    profile_id = ModelProfileId(os.environ["EDA_MODEL_PROFILE"])
    contract_path = tmp_path / "model-contract.json"
    calibration_path = tmp_path / "tokenizer-calibration.json"

    subprocess.run(  # noqa: S603
        [
            sys.executable,
            "scripts/check-model-contract.py",
            "--project-endpoint",
            project_endpoint,
            "--deployment",
            deployment,
            "--model-profile",
            profile_id.value,
            "--output",
            str(contract_path),
        ],
        check=True,
    )
    subprocess.run(  # noqa: S603
        [
            sys.executable,
            "scripts/calibrate-tokenizer.py",
            "--endpoint",
            resource_endpoint,
            "--deployment",
            deployment,
            "--model-profile",
            profile_id.value,
            "--output",
            str(calibration_path),
        ],
        check=True,
    )

    contract = ModelContract.model_validate_json(contract_path.read_text(encoding="utf-8"))
    calibration = Calibration.model_validate_json(calibration_path.read_text(encoding="utf-8"))
    profile = MODEL_PROFILES[profile_id]
    prompt = load_prompt(profile_id)
    assert contract.model_profile is profile_id
    assert contract.deployment == deployment
    assert contract.base_model == profile.expected_base_model
    assert contract.base_model_snapshot == profile.expected_snapshot
    assert contract.prompt_version == prompt.version
    assert contract.prompt_sha256 == prompt.sha256
    assert set(contract.verified_profiles) == set(WorkClass)
    assert contract.agent_framework_commit == "ad26cfe8c7cb4d75a701eed13f6b5021cf1ad3ed"
    analysis_evidence = set(contract.verified_profiles[WorkClass.ANALYSIS].response_evidence)
    assert "marker:automatic_two_tool_route" in analysis_evidence
    assert "marker:forced_first_reset" in analysis_evidence
    assert version("agent-framework-foundry") == "1.10.3"
    assert calibration.deployment == contract.deployment
    assert calibration.model_profile is contract.model_profile
    assert calibration.base_model == contract.base_model

    artifact = contract_path.read_text(encoding="utf-8")
    assert prompt.text not in artifact
    assert project_endpoint not in artifact
    assert resource_endpoint not in artifact
    assert re.search(r"https?://", artifact) is None
    for forbidden in ("phase complete", "tool route complete", "encrypted_content", "protected_data"):
        assert forbidden not in artifact
