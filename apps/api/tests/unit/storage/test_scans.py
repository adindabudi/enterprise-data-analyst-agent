from __future__ import annotations

import pytest
from eda_api.storage.scans import CLEAN, ERROR, MALICIOUS, NOT_SCANNED, SCAN_RESULT_TAG, ScanService


@pytest.fixture
def scan_service() -> ScanService:
    return ScanService()


@pytest.mark.parametrize("result", [None, ERROR, NOT_SCANNED, MALICIOUS])
def test_non_clean_scan_never_promotes(result: str | None, scan_service: ScanService) -> None:
    tags = {} if result is None else {SCAN_RESULT_TAG: result}

    outcome = scan_service.classify(tags)

    assert outcome.promotable is False


def test_only_no_threats_found_promotes(scan_service: ScanService) -> None:
    outcome = scan_service.classify({SCAN_RESULT_TAG: CLEAN})

    assert outcome.promotable is True
