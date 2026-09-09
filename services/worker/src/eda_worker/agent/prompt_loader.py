from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from importlib.resources import files

from eda_worker.model.profiles import MODEL_PROFILES, ModelProfileId


@dataclass(frozen=True)
class LoadedPrompt:
    profile_id: ModelProfileId
    version: str
    text: str
    sha256: str


def load_prompt(profile_id: ModelProfileId) -> LoadedPrompt:
    profile = MODEL_PROFILES[profile_id]
    resource = files("eda_worker.agent.prompts").joinpath(profile.prompt_file)
    body = resource.read_bytes()
    if not body or len(body) > 16_384:
        raise ValueError("prompt asset must be 1-16384 UTF-8 bytes")
    if body.startswith(b"\xef\xbb\xbf") or b"\r" in body or not body.endswith(b"\n"):
        raise ValueError("prompt asset must be BOM-free UTF-8 with LF endings and a final newline")
    text = body.decode("utf-8", errors="strict")
    if "{{" in text or "}}" in text:
        raise ValueError("runtime prompt templating is not allowed")
    return LoadedPrompt(
        profile_id=profile_id,
        version=profile.prompt_version,
        text=text,
        sha256=sha256(body).hexdigest(),
    )
