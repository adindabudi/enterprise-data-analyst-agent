from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_fabric_entra_setup_uses_isolated_contexts_and_no_client_secret() -> None:
    source = (ROOT / "scripts/configure-fabric-entra.sh").read_text(encoding="utf-8")
    assert "set -euo pipefail" in source
    assert "AZURE_CONFIG_DIR" in source
    assert "bootstrap" in source
    assert "finalize" in source
    assert "Item.Read.All" in source
    assert "Item.Execute.All" in source
    assert "az ad app credential reset" in source
    assert "--cert" in source
    assert "--append" in source
    assert "--password" not in source
    assert "client-secret" not in source.lower()
    assert "chmod 600" in source
    assert "trap" in source
