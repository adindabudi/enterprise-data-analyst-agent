from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal, cast

import httpx
from azure.identity.aio import AzureCliCredential, ManagedIdentityCredential
from eda_worker.model.profiles import MODEL_PROFILES, ModelProfileId
from eda_worker.model.tokenizer import Calibration

AI_SCOPE = "https://ai.azure.com/.default"
COGNITIVE_SCOPE = "https://cognitiveservices.azure.com/.default"
CORPUS = Path("services/worker/tests/fixtures/tokenizer-corpus.json")
SOURCE_COMMIT = "ad26cfe8c7cb4d75a701eed13f6b5021cf1ad3ed"


@dataclass(frozen=True)
class CountRequest:
    source: Literal["anthropic_messages", "responses_usage_input_tokens"]
    scope: str
    url: str
    body: dict[str, object]


def count_request(profile_id: ModelProfileId, *, endpoint: str, deployment: str, text: str) -> CountRequest:
    base = endpoint.rstrip("/")
    if profile_id is ModelProfileId.CLAUDE_OPUS_4_8_XHIGH_V1:
        return CountRequest(
            source="anthropic_messages",
            scope=AI_SCOPE,
            url=f"{base}/anthropic/v1/messages/count_tokens",
            body={"model": deployment, "messages": [{"role": "user", "content": text}]},
        )
    if profile_id is ModelProfileId.GPT_5_6_TERRA_MEDIUM_V1:
        return CountRequest(
            source="responses_usage_input_tokens",
            scope=COGNITIVE_SCOPE,
            url=f"{base}/openai/responses?api-version=2025-04-01-preview",
            body={"model": deployment, "input": text, "max_output_tokens": 16, "store": False},
        )
    raise ValueError(f"unsupported model profile: {profile_id}")


async def calibrate(
    *,
    endpoint: str,
    deployment: str,
    profile_id: ModelProfileId,
    output: Path,
    managed_identity_client_id: str | None,
) -> int:
    corpus_bytes = await asyncio.to_thread(CORPUS.read_bytes)
    raw_samples = json.loads(corpus_bytes)
    if not isinstance(raw_samples, list):
        raise RuntimeError("tokenizer corpus must be an array")
    samples = [cast(dict[str, object], sample) for sample in cast(list[object], raw_samples)]
    profile = MODEL_PROFILES[profile_id]
    credential = (
        ManagedIdentityCredential(client_id=managed_identity_client_id)
        if managed_identity_client_id is not None
        else AzureCliCredential()
    )
    try:
        selected_scope = COGNITIVE_SCOPE if profile_id is ModelProfileId.GPT_5_6_TERRA_MEDIUM_V1 else AI_SCOPE
        token = await credential.get_token(selected_scope)
        ratios: list[float] = []
        selected_source: Literal["anthropic_messages", "responses_usage_input_tokens"] | None = None
        async with httpx.AsyncClient(timeout=httpx.Timeout(120, connect=10)) as client:
            for sample in samples:
                text = sample.get("text")
                if not isinstance(text, str) or not text:
                    raise RuntimeError("tokenizer corpus contains invalid text")
                request = count_request(profile_id, endpoint=endpoint, deployment=deployment, text=text)
                if selected_source is not None and request.source != selected_source:
                    raise RuntimeError("tokenizer calibration count source changed during one run")
                if request.scope != selected_scope:
                    raise RuntimeError("tokenizer calibration token scope changed during one run")
                selected_source = request.source
                response = await client.post(
                    request.url,
                    headers={"Authorization": f"Bearer {token.token}"},
                    json=request.body,
                )
                response.raise_for_status()
                payload: dict[str, Any] = response.json()
                if request.source == "responses_usage_input_tokens":
                    usage = payload.get("usage")
                    count = cast("dict[str, Any]", usage).get("input_tokens") if isinstance(usage, dict) else None
                else:
                    count = payload.get("input_tokens")
                if not isinstance(count, int) or count < 1:
                    raise RuntimeError("count_tokens returned an invalid token count")
                ratios.append(len(text) / count)
        if selected_source is None:
            raise RuntimeError("tokenizer corpus is empty")
        result = Calibration(
            deployment=deployment,
            model_profile=profile_id,
            base_model=profile.expected_base_model,
            count_source=selected_source,
            chars_per_token=min(4.0, min(ratios)),
            safety_margin=1.25,
            samples=len(samples),
            ratios=tuple(ratios),
            corpus_sha256=sha256(corpus_bytes).hexdigest(),
            source_commit=SOURCE_COMMIT,
        )
        await asyncio.to_thread(
            output.write_text,
            result.model_dump_json(by_alias=True, exclude_none=True) + "\n",
            encoding="utf-8",
        )
        return len(samples)
    finally:
        await credential.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--deployment", required=True)
    parser.add_argument("--model-profile", default=os.environ.get("EDA_MODEL_PROFILE"), choices=list(ModelProfileId))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--managed-identity-client-id")
    args = parser.parse_args()
    if args.model_profile is None:
        parser.error("--model-profile or EDA_MODEL_PROFILE is required")
    samples = asyncio.run(
        calibrate(
            endpoint=args.endpoint,
            deployment=args.deployment,
            profile_id=ModelProfileId(args.model_profile),
            output=args.output,
            managed_identity_client_id=args.managed_identity_client_id,
        )
    )
    print(f"PASS: calibrated {samples} samples")


if __name__ == "__main__":
    main()
