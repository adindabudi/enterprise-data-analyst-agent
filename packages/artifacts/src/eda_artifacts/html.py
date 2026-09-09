from __future__ import annotations

import re
from html.parser import HTMLParser

_REQUIRED_CSP = {
    "default-src": ("'none'",),
    "script-src": ("'unsafe-inline'",),
    "style-src": ("'unsafe-inline'",),
    "img-src": ("data:", "blob:"),
    "font-src": ("data:",),
    "connect-src": ("'none'",),
    "object-src": ("'none'",),
    "base-uri": ("'none'",),
    "form-action": ("'none'",),
}
_FORBIDDEN_TAGS = frozenset({"base", "embed", "form", "iframe", "object"})
_RESOURCE_ATTRIBUTES = frozenset({"action", "formaction", "href", "poster", "src", "srcset"})
_CSS_NETWORK_REFERENCE = re.compile(
    r"(?i)(?:url\s*\(\s*['\"]?\s*(?:https?:)?//|@import\s+(?:url\s*\(\s*)?['\"]?\s*(?:https?:)?//)"
)


class _WebArtifactParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.csp: str | None = None
        self.saw_script = False
        self._in_style = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized_tag = tag.casefold()
        attributes = {name.casefold(): value or "" for name, value in attrs}
        if normalized_tag in _FORBIDDEN_TAGS:
            raise ValueError("web artifact contains a forbidden active element")
        if normalized_tag == "meta":
            http_equiv = attributes.get("http-equiv", "").casefold()
            if http_equiv == "refresh":
                raise ValueError("web artifact contains a forbidden redirect")
            if http_equiv == "content-security-policy":
                if self.csp is not None or self.saw_script:
                    raise ValueError("web artifact CSP must appear once before scripts")
                self.csp = attributes.get("content", "")
        if normalized_tag == "script":
            if self.csp is None:
                raise ValueError("web artifact CSP must appear before scripts")
            self.saw_script = True
        if normalized_tag == "style":
            self._in_style = True
        for name, value in attributes.items():
            if name in _RESOURCE_ATTRIBUTES and value and not _is_embedded_reference(value):
                raise ValueError("web artifact contains a non-embedded resource reference")
            if name == "style" and _CSS_NETWORK_REFERENCE.search(value):
                raise ValueError("web artifact CSS contains a remote network reference")

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "style":
            self._in_style = False

    def handle_data(self, data: str) -> None:
        if self._in_style and _CSS_NETWORK_REFERENCE.search(data):
            raise ValueError("web artifact CSS contains a remote network reference")


def _is_embedded_reference(value: str) -> bool:
    normalized = value.strip().casefold()
    return normalized.startswith(("#", "data:", "blob:"))


def _parse_csp(value: str) -> dict[str, tuple[str, ...]]:
    directives: dict[str, tuple[str, ...]] = {}
    for raw_directive in value.casefold().split(";"):
        parts = raw_directive.split()
        if not parts:
            continue
        name, directive_values = parts[0], tuple(parts[1:])
        if name in directives:
            raise ValueError("web artifact CSP contains a duplicate directive")
        directives[name] = directive_values
    return directives


def validate_html(document: str) -> None:
    lowered = document.lower()
    if (
        "<script" in lowered
        or "<form" in lowered
        or "http://" in lowered
        or "https://" in lowered
        or "onload=" in lowered
        or "onclick=" in lowered
    ):
        raise ValueError("HTML contains active or remote content")


def validate_web_artifact_html(document: str) -> None:
    parser = _WebArtifactParser()
    parser.feed(document)
    parser.close()
    if parser.csp is None:
        raise ValueError("web artifact requires a content security policy")
    directives = _parse_csp(parser.csp)
    if directives != _REQUIRED_CSP:
        raise ValueError("web artifact content security policy is not restrictive enough")
