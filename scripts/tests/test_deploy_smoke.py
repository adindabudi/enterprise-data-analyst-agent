from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_hosted_readiness_accepts_the_versioned_azd_responses_endpoint() -> None:
    source = (ROOT / "scripts/deploy-smoke.sh").read_text(encoding="utf-8")

    assert "https://*/responses | https://*/responses\\?api-version=v1" in source