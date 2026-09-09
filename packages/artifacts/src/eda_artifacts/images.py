from __future__ import annotations

import struct

from defusedxml import ElementTree
from PIL import Image


def validate_png(payload: bytes, *, max_pixels: int = 100_000_000) -> tuple[int, int]:
    from io import BytesIO

    with Image.open(BytesIO(payload)) as image:
        image.verify()
    with Image.open(BytesIO(payload)) as image:
        width, height = image.size
    if width * height > max_pixels:
        raise ValueError("image exceeds pixel limit")
    _validate_png_end(payload)
    return width, height


def validate_svg(document: str) -> None:
    try:
        root = ElementTree.fromstring(document)
    except ElementTree.ParseError as error:
        raise ValueError("SVG is not well-formed XML") from error
    if _local_name(root.tag) != "svg":
        raise ValueError("SVG root element is missing")
    for element in root.iter():
        tag = _local_name(element.tag).lower()
        if tag in {"script", "foreignobject"}:
            raise ValueError("SVG contains active content")
        if tag == "style" and _contains_remote_css(element.text or ""):
            raise ValueError("SVG contains remote CSS")
        for raw_name, raw_value in element.attrib.items():
            name = _local_name(raw_name).lower()
            value = raw_value.strip()
            if name.startswith("on"):
                raise ValueError("SVG contains an event handler")
            if name == "href" and value and not value.startswith("#"):
                raise ValueError("SVG contains an external reference")
            if name == "style" and _contains_remote_css(value):
                raise ValueError("SVG contains remote CSS")


def _local_name(value: str) -> str:
    return value.rsplit("}", 1)[-1]


def _contains_remote_css(value: str) -> bool:
    lowered = value.lower()
    return "@import" in lowered or "url(http:" in lowered or "url(https:" in lowered or "expression(" in lowered


def _validate_png_end(payload: bytes) -> None:
    if not payload.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("invalid PNG signature")
    offset = 8
    while offset + 12 <= len(payload):
        length = struct.unpack(">I", payload[offset : offset + 4])[0]
        chunk_type = payload[offset + 4 : offset + 8]
        offset += 12 + length
        if offset > len(payload):
            raise ValueError("truncated PNG chunk")
        if chunk_type == b"IEND":
            if offset != len(payload):
                raise ValueError("PNG contains trailing data")
            return
    raise ValueError("PNG missing IEND")
