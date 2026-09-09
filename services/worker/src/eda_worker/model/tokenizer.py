from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .profiles import ModelProfileId, camel_case


class Calibration(BaseModel):
    model_config = ConfigDict(alias_generator=camel_case, populate_by_name=True, extra="forbid", frozen=True)

    schema_version: str = "1.0"
    deployment: str | None = None
    model_profile: ModelProfileId
    base_model: Literal["claude-opus-4-8", "gpt-5.6-terra"]
    count_source: Literal["anthropic_messages", "responses_usage_input_tokens"]
    chars_per_token: float = Field(gt=0.5, le=4.0)
    safety_margin: float = Field(ge=1.1, le=2.0)
    samples: int = Field(ge=20)
    ratios: tuple[float, ...] = ()
    corpus_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    source_commit: str | None = Field(default=None, pattern=r"^[a-f0-9]{40}$")

    @model_validator(mode="after")
    def validate_ratios(self) -> Calibration:
        if self.ratios and len(self.ratios) != self.samples:
            raise ValueError("tokenizer calibration ratios must match sample count")
        return self


class CalibratedTokenizer:
    def __init__(self, calibration: Calibration) -> None:
        self.calibration = calibration

    def count_tokens(self, text: str) -> int:
        estimated = len(text) / self.calibration.chars_per_token
        return max(1, math.ceil(estimated * self.calibration.safety_margin))
