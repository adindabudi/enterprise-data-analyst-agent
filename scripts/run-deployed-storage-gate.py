from __future__ import annotations

import os
import sys

import pytest

COSMOS_SETTINGS = (
    "EDA_COSMOS_ENDPOINT",
    "EDA_COSMOS_DATABASE",
    "EDA_COSMOS_AUTH_CONTAINER",
    "EDA_COSMOS_WORKSPACE_CONTAINER",
    "EDA_COSMOS_RUNTIME_CONTAINER",
)
BLOB_SETTINGS = ("EDA_BLOB_ACCOUNT_URL", "EDA_BLOB_QUARANTINE_CONTAINER")
RUNNER_REQUIREMENT = (
    "Use a private-network runner with Azure CLI signed in as the current user and the required data-plane roles."
)


class CloudResults:
    def __init__(self) -> None:
        self.passed = 0

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        if report.when == "call" and report.passed and "cloud" in report.keywords:
            self.passed += 1


def main() -> int:
    required = COSMOS_SETTINGS
    if os.getenv("EDA_RUN_DEFENDER_ACCEPTANCE") == "1":
        required += BLOB_SETTINGS
    missing = [name for name in required if not os.getenv(name, "").strip()]
    if missing:
        print(
            f"FAIL: deployed-storage-gate missing required settings: {', '.join(missing)}. "
            f"Export them for the selected deployed environment. {RUNNER_REQUIREMENT}",
            file=sys.stderr,
        )
        return 1

    results = CloudResults()
    exit_code = pytest.main(["-m", "cloud", *sys.argv[1:], "-rs"], plugins=[results])
    if exit_code in (pytest.ExitCode.OK, pytest.ExitCode.NO_TESTS_COLLECTED) and results.passed == 0:
        print(
            "FAIL: deployed-storage-gate: no cloud tests passed; skipped or unselected tests are not acceptance. "
            f"Check the selected tests and their prerequisites. {RUNNER_REQUIREMENT}",
            file=sys.stderr,
        )
        return int(exit_code) or 1
    return int(exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
