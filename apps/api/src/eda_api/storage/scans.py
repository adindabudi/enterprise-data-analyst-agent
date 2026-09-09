from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

ScanState = Literal["scanning", "clean", "rejected", "scan_failed"]

SCAN_RESULT_TAG = "Malware Scanning scan result"
CLEAN = "No threats found"
MALICIOUS = "Malicious"
ERROR = "Error"
NOT_SCANNED = "Not scanned"


@dataclass(frozen=True)
class ScanOutcome:
    state: ScanState
    promotable: bool


class ScanService:
    def classify(self, tags: Mapping[str, str]) -> ScanOutcome:
        result = tags.get(SCAN_RESULT_TAG)
        if result == CLEAN:
            return ScanOutcome(state="clean", promotable=True)
        if result == MALICIOUS:
            return ScanOutcome(state="rejected", promotable=False)
        if result in {ERROR, NOT_SCANNED}:
            return ScanOutcome(state="scan_failed", promotable=False)
        return ScanOutcome(state="scanning", promotable=False)
