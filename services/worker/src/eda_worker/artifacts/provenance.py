from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class ProvenanceError(ValueError):
    pass


@dataclass(frozen=True)
class Claim:
    text: str
    important: bool
    evidence_refs: tuple[str, ...]


class TaskManifestBuilder:
    def __init__(self, *, task_id: str) -> None:
        self._task_id = task_id
        self._claims: list[Claim] = []

    def add_claim(self, text: str, *, important: bool, evidence_refs: list[str]) -> None:
        if important and not evidence_refs:
            raise ProvenanceError("important claim requires evidence")
        self._claims.append(Claim(text=text, important=important, evidence_refs=tuple(evidence_refs)))

    def build(self, runtime_state: dict[str, Any]) -> dict[str, Any]:
        allowed_runtime = {
            key: value
            for key, value in runtime_state.items()
            if key in {"promptVersion", "contextVersion", "modelContract", "imageDigest"}
        }
        return {
            "taskId": self._task_id,
            "runtime": allowed_runtime,
            "claims": [
                {"text": claim.text, "important": claim.important, "evidenceRefs": list(claim.evidence_refs)}
                for claim in self._claims
            ],
        }
