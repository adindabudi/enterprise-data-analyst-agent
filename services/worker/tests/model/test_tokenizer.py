import json

import pytest
from eda_worker.model.tokenizer import CalibratedTokenizer, Calibration
from pydantic import ValidationError


def test_counter_rounds_up_with_safety_margin() -> None:
    tokenizer = CalibratedTokenizer(
        Calibration(
            deployment="analysis-terra",
            model_profile="gpt-5.6-terra-medium-v1",
            base_model="gpt-5.6-terra",
            count_source="responses_usage_input_tokens",
            chars_per_token=3.5,
            safety_margin=1.25,
            samples=20,
        )
    )

    assert tokenizer.count_tokens("x" * 350) == 125


def test_calibration_rejects_optimistic_or_tiny_corpus() -> None:
    with pytest.raises(ValidationError):
        Calibration(
            deployment="analysis-terra",
            model_profile="gpt-5.6-terra-medium-v1",
            base_model="gpt-5.6-terra",
            count_source="responses_usage_input_tokens",
            chars_per_token=5.0,
            safety_margin=0.9,
            samples=2,
        )


def test_calibration_rejects_missing_profile_identity() -> None:
    with pytest.raises(ValidationError):
        Calibration(chars_per_token=3.5, safety_margin=1.25, samples=20)


def test_checked_in_corpus_has_required_shapes() -> None:
    corpus = json.loads(open("services/worker/tests/fixtures/tokenizer-corpus.json", encoding="utf-8").read())

    assert len(corpus) >= 20
    assert {item["kind"] for item in corpus} >= {"prose", "json", "python", "table", "tool_result"}
