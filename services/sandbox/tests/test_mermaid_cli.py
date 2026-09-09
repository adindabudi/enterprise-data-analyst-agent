from __future__ import annotations

import subprocess
from pathlib import Path
from shutil import which

NODE = which("node") or "/usr/bin/node"


def test_local_mermaid_cli_exposes_supported_options() -> None:
    script = Path("services/sandbox/scripts/render-mermaid.mjs")

    result = subprocess.run(  # noqa: S603 - fixed Node executable and repository-owned script.
        [NODE, str(script), "--help"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "-i, --input" in result.stdout
    assert "-o, --output" in result.stdout
    assert "-p, --puppeteerConfigFile" in result.stdout
    assert "-c, --configFile" in result.stdout


def test_local_mermaid_cli_forces_strict_security_after_config_merge() -> None:
    source = Path("services/sandbox/scripts/render-mermaid.mjs").read_text(encoding="utf-8")

    config_merge = source.index("...config")
    strict_mode = source.index('securityLevel: "strict"', config_merge)
    render_call = source.index("globalThis.mermaid.render", config_merge)

    assert config_merge < strict_mode < render_call
