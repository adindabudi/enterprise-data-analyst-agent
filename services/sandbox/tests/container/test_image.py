from __future__ import annotations

import json
import os
import subprocess
from shutil import which

import pytest

DOCKER = which("docker") or "/usr/bin/docker"


@pytest.fixture
def image_name() -> str:
    value = os.getenv("EDA_SANDBOX_IMAGE")
    if value is None:
        pytest.skip("EDA_SANDBOX_IMAGE is required for image policy tests")
    return value


@pytest.fixture
def image_inspect(image_name: str) -> dict[str, object]:
    result = subprocess.run(  # noqa: S603 -- fixed docker inspect command in test.
        [DOCKER, "image", "inspect", image_name],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)[0]


def test_image_is_nonroot(image_inspect: dict[str, object]) -> None:
    config = image_inspect["Config"]
    assert isinstance(config, dict)
    assert config["User"] == "10001:10001"


def test_required_binaries_exist(image_name: str) -> None:
    result = subprocess.run(  # noqa: S603 -- fixed docker command in test.
        [
            DOCKER,
            "run",
            "--rm",
            "--entrypoint",
            "/bin/sh",
            image_name,
            "-c",
            "which python node libreoffice pdftoppm pandoc mmdc chromium",
        ],
        check=False,
    )
    assert result.returncode == 0
