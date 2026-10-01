from __future__ import annotations

from typing import Literal

AdmissionReason = Literal["queue_full", "owner_limit", "runtime_unavailable"]


class AdmissionRejected(RuntimeError):
    """The runtime cannot take this task now; nothing about it was persisted, so it may be retried."""

    def __init__(self, reason: AdmissionReason) -> None:
        super().__init__(f"analysis admission rejected: {reason}")
        self.reason: AdmissionReason = reason
