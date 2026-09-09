from __future__ import annotations


def validate_mermaid(source: str) -> None:
    if not source.strip():
        raise ValueError("Mermaid source is empty")
