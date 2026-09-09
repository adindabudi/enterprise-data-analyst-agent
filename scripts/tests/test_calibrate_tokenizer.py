from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

from eda_worker.model.profiles import ModelProfileId

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "calibrate-tokenizer.py"


def load_module() -> Any:
    specification = importlib.util.spec_from_file_location("calibrate_tokenizer", SCRIPT)
    assert specification is not None
    assert specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def test_terra_uses_bounded_responses_usage_contract() -> None:
    module = load_module()

    request = module.count_request(
        ModelProfileId.GPT_5_6_TERRA_MEDIUM_V1,
        endpoint="https://example.openai.azure.com",
        deployment="analysis-terra",
        text="hello",
    )

    assert request.source == "responses_usage_input_tokens"
    assert request.scope == "https://cognitiveservices.azure.com/.default"
    assert request.url.endswith("/openai/responses?api-version=2025-04-01-preview")
    assert request.body == {
        "model": "analysis-terra",
        "input": "hello",
        "max_output_tokens": 16,
        "store": False,
    }


def test_calibrator_allows_bounded_reasoning_model_latency() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "httpx.Timeout(120, connect=10)" in source
    assert "httpx.AsyncClient(timeout=30)" not in source


def test_opus_uses_anthropic_count_contract_without_fallback() -> None:
    module = load_module()

    request = module.count_request(
        ModelProfileId.CLAUDE_OPUS_4_8_XHIGH_V1,
        endpoint="https://example.services.ai.azure.com/api/projects/example",
        deployment="analysis-opus",
        text="hello",
    )

    assert request.source == "anthropic_messages"
    assert request.scope == "https://ai.azure.com/.default"
    assert request.url.endswith("/anthropic/v1/messages/count_tokens")
    assert request.body == {
        "model": "analysis-opus",
        "messages": [{"role": "user", "content": "hello"}],
    }
