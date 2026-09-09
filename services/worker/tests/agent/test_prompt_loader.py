from __future__ import annotations

from hashlib import sha256
from importlib.resources import files
from pathlib import Path

import pytest
from eda_worker.agent import prompt_loader
from eda_worker.model.profiles import ModelProfileId


@pytest.mark.parametrize(
    ("profile_id", "filename", "version", "expected_sha256"),
    [
        (
            ModelProfileId.CLAUDE_OPUS_4_8_XHIGH_V1,
            "claude-opus-4-8-v1.md",
            "claude-opus-4-8-v1",
            "9e4408e257c61ba7f299ca0512545915f37703f9fb40aeb3416a57b9c372ecb4",
        ),
        (
            ModelProfileId.GPT_5_6_TERRA_MEDIUM_V1,
            "gpt-5.6-terra-v1.md",
            "gpt-5.6-terra-v1",
            "c70463cba2aec050c0b05c95a9ed0fdbe9872c9f8ab228ad4d1550b23d205409",
        ),
    ],
)
def test_prompt_asset_is_exact(
    profile_id: ModelProfileId,
    filename: str,
    version: str,
    expected_sha256: str,
) -> None:
    body = files("eda_worker.agent.prompts").joinpath(filename).read_bytes()
    loaded = prompt_loader.load_prompt(profile_id)

    assert loaded.profile_id is profile_id
    assert loaded.version == version
    assert loaded.text.encode("utf-8") == body
    assert sha256(body).hexdigest() == expected_sha256 == loaded.sha256
    assert 1 <= len(body) <= 16_384
    assert not body.startswith(b"\xef\xbb\xbf")
    assert b"\r" not in body
    assert body.endswith(b"\n")


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"\xef\xbb\xbftext\n",
        b"text\r\n",
        b"text",
        b"{{ runtime }}\n",
        b"runtime }}\n",
        b"\xff\n",
        (b"x" * 16_384) + b"\n",
    ],
)
def test_prompt_loader_rejects_noncanonical_bytes(
    body: bytes,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile_id = ModelProfileId.GPT_5_6_TERRA_MEDIUM_V1
    profile = prompt_loader.MODEL_PROFILES[profile_id]
    tmp_path.joinpath(profile.prompt_file).write_bytes(body)

    def resource_root(_: str) -> Path:
        return tmp_path

    monkeypatch.setattr(prompt_loader, "files", resource_root)
    with pytest.raises((ValueError, UnicodeError)):
        prompt_loader.load_prompt(profile_id)


def test_terra_prompt_requires_the_reviewed_web_artifact_pipeline() -> None:
    prompt = prompt_loader.load_prompt(ModelProfileId.GPT_5_6_TERRA_MEDIUM_V1).text

    assert "build_web_artifact" in prompt
    assert "web_artifact_html" in prompt
    assert prompt.index("build_web_artifact") < prompt.index("web_artifact_html") < prompt.index("publish_artifact")
