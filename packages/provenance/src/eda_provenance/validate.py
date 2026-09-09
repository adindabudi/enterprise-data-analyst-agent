from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

from .models import TaskManifest

FORBIDDEN_KEYS = {
    "accesstoken",
    "access_token",
    "apikey",
    "api_key",
    "authorization",
    "bearer",
    "clientsecret",
    "client_secret",
    "connectionstring",
    "connection_string",
    "cookie",
    "credential",
    "credentials",
    "jwt",
    "pat",
    "password",
    "privatekey",
    "private_key",
    "refreshtoken",
    "refresh_token",
    "sastoken",
    "sas_token",
    "secret",
    "sessiontoken",
    "session_token",
}

MAX_MANIFEST_NESTING_DEPTH = 64


class ManifestValidationError(ValueError):
    pass


def _normalize_key(key: object) -> str:
    text = str(key).strip().lower()
    for separator in ("-", " ", ".", ":"):
        text = text.replace(separator, "_")
    return text


def _is_forbidden(normalized: str) -> bool:
    padded = f"_{normalized}_"
    if any(f"_{forbidden}_" in padded for forbidden in FORBIDDEN_KEYS):
        return True
    joined = normalized.replace("_", "")
    return any(forbidden.replace("_", "") == joined for forbidden in FORBIDDEN_KEYS)


def walk_for_forbidden_keys(value: Any, path: str = "$", depth: int = 0) -> None:
    if depth > MAX_MANIFEST_NESTING_DEPTH:
        raise ManifestValidationError(
            f"manifest nesting exceeds the maximum depth of {MAX_MANIFEST_NESTING_DEPTH} at {path}"
        )
    if isinstance(value, Mapping):
        mapping = cast("Mapping[Any, Any]", value)
        for key, child in mapping.items():
            if _is_forbidden(_normalize_key(key)):
                raise ManifestValidationError(f"credential-shaped key at {path}.{key!r}")
            walk_for_forbidden_keys(child, f"{path}.{key}", depth + 1)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        sequence = cast("Sequence[Any]", value)
        for index, child in enumerate(sequence):
            walk_for_forbidden_keys(child, f"{path}[{index}]", depth + 1)


def assert_manifest_safe(value: Mapping[str, Any]) -> TaskManifest:
    walk_for_forbidden_keys(value)
    return TaskManifest.model_validate(value)
