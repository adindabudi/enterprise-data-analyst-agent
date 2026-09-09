from __future__ import annotations

from typing import Any


def validate_manifest(manifest: dict[str, Any]) -> None:
    if not manifest.get("artifacts"):
        raise ValueError("manifest requires artifacts")
