from __future__ import annotations

import json
import sys

import pytest
from eda_sandbox import cli


def test_cli_writes_a_passing_validation_report(monkeypatch: pytest.MonkeyPatch, tmp_path, capsys) -> None:
    candidate = tmp_path / "report.html"
    candidate.write_text("<!doctype html><html><body>ready</body></html>", encoding="utf-8")
    report = tmp_path / "validation.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "eda-sandbox",
            "validate",
            "--file",
            str(candidate),
            "--profile",
            "core_html",
            "--output",
            str(report),
        ],
    )

    cli.main()

    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["status"] == "passed"
    assert payload["profile"] == "core_html"
    assert json.loads(capsys.readouterr().out)["status"] == "passed"


def test_cli_exits_nonzero_and_writes_a_failed_report(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    candidate = tmp_path / "report.html"
    candidate.write_text("<script>alert(1)</script>", encoding="utf-8")
    report = tmp_path / "validation.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "eda-sandbox",
            "validate",
            "--file",
            str(candidate),
            "--profile",
            "core_html",
            "--output",
            str(report),
        ],
    )

    with pytest.raises(SystemExit, match="1"):
        cli.main()

    assert json.loads(report.read_text(encoding="utf-8"))["status"] == "failed"


def test_cli_validates_an_interactive_web_artifact_with_its_dedicated_profile(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    candidate = tmp_path / "dashboard.html"
    candidate.write_text(
        '<meta http-equiv="Content-Security-Policy" '
        "content=\"default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
        "img-src data: blob:; font-src data:; connect-src 'none'; object-src 'none'; "
        "base-uri 'none'; form-action 'none'\">"
        "<button id='filter'>Filter</button>"
        "<script>document.querySelector('#filter').addEventListener('click', () => {});</script>",
        encoding="utf-8",
    )
    report = tmp_path / "validation.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "eda-sandbox",
            "validate",
            "--file",
            str(candidate),
            "--profile",
            "web_artifact_html",
            "--output",
            str(report),
        ],
    )

    cli.main()

    assert json.loads(report.read_text(encoding="utf-8"))["status"] == "passed"


def test_cli_bundles_web_source_with_the_baked_toolchain(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    app = tmp_path / "App.tsx"
    app.write_text("export default function App(){return <main>Revenue</main>}", encoding="utf-8")
    styles = tmp_path / "index.css"
    styles.write_text("main { display: grid; }", encoding="utf-8")
    output = tmp_path / "dashboard.html"
    calls: list[dict[str, object]] = []

    def bundle(**arguments: object) -> None:
        calls.append(arguments)

    monkeypatch.setattr(cli, "bundle_web_artifact", bundle, raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "eda-sandbox",
            "bundle-web",
            "--app",
            str(app),
            "--styles",
            str(styles),
            "--title",
            "Executive dashboard",
            "--output",
            str(output),
        ],
    )

    cli.main()

    assert calls == [
        {
            "app_source": app.read_text(encoding="utf-8"),
            "styles": styles.read_text(encoding="utf-8"),
            "title": "Executive dashboard",
            "output": output,
        }
    ]
