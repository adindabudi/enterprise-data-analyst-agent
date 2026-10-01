from __future__ import annotations

import sys

import eda_worker.main as worker_main
import pytest
from eda_worker.cleanup import CleanupFailure, CleanupResult
from eda_worker.main import health


def test_worker_health_is_content_free() -> None:
    assert health() == {"status": "alive", "component": "worker"}


def test_cleanup_cli_accepts_scheduled_arguments_and_exits_nonzero_on_partial_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: dict[str, object] = {}

    async def partial_cleanup(*, before: str, limit: int, dry_run: bool) -> CleanupResult:
        called.update(before=before, limit=limit, dry_run=dry_run)
        return CleanupResult(
            deleted_sessions=1,
            dry_run=False,
            failures=(CleanupFailure(partition_key=("tenant", "owner", "ses_1234567890abcdef")),),
        )

    monkeypatch.setattr(worker_main, "configure_telemetry", lambda: None)
    monkeypatch.setattr(worker_main, "run_cleanup", partial_cleanup)
    monkeypatch.setattr(sys, "argv", ["eda-worker", "cleanup", "--before", "now", "--limit", "100"])

    with pytest.raises(SystemExit, match="1"):
        worker_main.main()

    assert called == {"before": "now", "limit": 100, "dry_run": False}


def test_publish_documents_cli_runs_private_contract_publication(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[bool] = []

    async def publish() -> str:
        calls.append(True)
        return "a" * 64

    monkeypatch.setattr(worker_main, "configure_telemetry", lambda: None)
    monkeypatch.setattr(worker_main, "run_document_contract_publication", publish, raising=False)
    monkeypatch.setattr(sys, "argv", ["eda-worker", "publish-documents"])

    worker_main.main()

    assert calls == [True]
