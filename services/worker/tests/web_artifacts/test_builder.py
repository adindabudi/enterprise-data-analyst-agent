from __future__ import annotations

import base64

from eda_worker.tools.contracts import BuildWebArtifactOperation, SandboxRuntime
from eda_worker.web_artifacts.builder import build_web_execution


def test_web_build_operation_is_bounded_and_uses_the_first_party_sandbox_cli() -> None:
    operation = BuildWebArtifactOperation(
        title="Executive dashboard",
        display_name="executive-dashboard.html",
        app_source="export default function App(){return <main>Revenue</main>}",
        styles="main { display: grid; }",
    )

    execution = build_web_execution(operation)

    assert execution.runtime is SandboxRuntime.PYTHON
    assert execution.timeout_seconds == 300
    assert execution.input_artifacts == ()
    assert "eda_sandbox.cli" in execution.source
    assert "bundle-web" in execution.source
    assert "executive-dashboard.html" in execution.source
    assert operation.app_source not in execution.source
    assert base64.b64encode(operation.app_source.encode()).decode() in execution.source
    assert "npm" not in execution.source
    assert "npx" not in execution.source
    assert "pnpm" not in execution.source
