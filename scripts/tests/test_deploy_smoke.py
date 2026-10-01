from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_analysis_runtime_readiness_uses_ready_component() -> None:
    source = (ROOT / "scripts/deploy-smoke.sh").read_text(encoding="utf-8")

    assert "analysis_runtime_ready()" in source
    assert ".components.analysisRuntime // empty" in source
    assert "run_gate analysis-runtime-readiness analysis_runtime_ready" in source
    assert "hosted_agent_ready" not in source
    assert "hosted-agent-readiness" not in source
