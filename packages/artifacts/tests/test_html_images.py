from __future__ import annotations

import io

import pytest
from eda_artifacts.html import validate_html, validate_web_artifact_html
from eda_artifacts.images import validate_png, validate_svg
from PIL import Image


@pytest.mark.parametrize(
    "document",
    [
        "<script>alert(1)</script>",
        '<img src="https://example.test/image.png">',
        '<form action="https://example.test"></form>',
    ],
)
def test_html_rejects_active_or_remote_content(document: str) -> None:
    with pytest.raises(ValueError):
        validate_html(document)


def test_web_artifact_allows_inline_interactivity_with_a_restrictive_csp() -> None:
    validate_web_artifact_html(
        "<!doctype html><html><head>"
        '<meta http-equiv="Content-Security-Policy" '
        "content=\"default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
        "img-src data: blob:; font-src data:; connect-src 'none'; object-src 'none'; "
        "base-uri 'none'; form-action 'none'\">"
        "</head><body><button id='filter'>Filter</button>"
        "<script>document.querySelector('#filter').addEventListener('click', () => {});</script>"
        "</body></html>"
    )


def test_web_artifact_allows_inert_library_urls_but_rejects_remote_css() -> None:
    policy = (
        '<meta http-equiv="Content-Security-Policy" '
        "content=\"default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
        "img-src data: blob:; font-src data:; connect-src 'none'; object-src 'none'; "
        "base-uri 'none'; form-action 'none'\">"
    )
    validate_web_artifact_html(
        policy
        + "<style>/*! library docs https://example.test/license */ main{display:grid}</style>"
        + "<script>const docs='https://react.dev/errors/1';</script>"
    )

    with pytest.raises(ValueError, match="remote network reference"):
        validate_web_artifact_html(policy + "<style>main{background:url(https://example.test/a.png)}</style>")


def test_web_artifact_rejects_csp_directives_that_reopen_network_access() -> None:
    document = (
        '<meta http-equiv="Content-Security-Policy" '
        "content=\"default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
        "img-src data: blob:; font-src data:; connect-src 'none'; object-src 'none'; "
        "base-uri 'none'; form-action 'none'; worker-src https://example.test\">"
        "<script>document.body.textContent='ready';</script>"
    )

    with pytest.raises(ValueError, match="not restrictive enough"):
        validate_web_artifact_html(document)


@pytest.mark.parametrize(
    "document",
    [
        "<html><body><script>document.body.textContent='unsafe'</script></body></html>",
        (
            '<meta http-equiv="Content-Security-Policy" '
            "content=\"default-src 'none'; script-src 'unsafe-inline'\">"
            '<script src="https://example.test/app.js"></script>'
        ),
        (
            '<meta http-equiv="Content-Security-Policy" '
            "content=\"default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
            "connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'\">"
            '<form action="/submit"></form>'
        ),
    ],
)
def test_web_artifact_rejects_missing_policy_remote_content_and_forms(document: str) -> None:
    with pytest.raises(ValueError):
        validate_web_artifact_html(document)


def test_svg_rejects_script_and_external_href() -> None:
    with pytest.raises(ValueError):
        validate_svg("<svg><script>alert(1)</script></svg>")
    with pytest.raises(ValueError):
        validate_svg('<svg><image href="https://example.test/image.png" /></svg>')


def test_svg_allows_standard_namespace_and_local_references() -> None:
    validate_svg(
        '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">'
        '<defs><path id="line" d="M0 0L10 10"/></defs><use href="#line"/></svg>'
    )


def test_png_validation_reports_safe_dimensions() -> None:
    buffer = io.BytesIO()
    Image.new("RGB", (2, 3)).save(buffer, format="PNG")

    assert validate_png(buffer.getvalue()) == (2, 3)


def test_png_rejects_trailing_data() -> None:
    buffer = io.BytesIO()
    Image.new("RGB", (1, 1)).save(buffer, format="PNG")

    with pytest.raises(ValueError):
        validate_png(buffer.getvalue() + b"unexpected")
