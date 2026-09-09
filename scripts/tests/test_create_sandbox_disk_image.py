from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from io import StringIO
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
CREATE_SCRIPT = ROOT / "scripts" / "create-sandbox-disk-image.py"
DEPLOY_SCRIPT = ROOT / "scripts" / "deploy-sandbox-group.sh"


def load_module() -> Any:
    specification = spec_from_file_location("create_sandbox_disk_image", CREATE_SCRIPT)
    assert specification is not None
    assert specification.loader is not None
    module = module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_registry_refresh_token_is_read_from_stdin() -> None:
    module = load_module()
    credential_value = "-".join(("short", "lived", "token"))

    credentials = module.read_registry_credentials(StringIO(f"{credential_value}\n"))

    assert credentials.username == "00000000-0000-0000-0000-000000000000"
    assert credentials.token == credential_value


def test_empty_registry_refresh_token_fails_closed() -> None:
    module = load_module()

    with pytest.raises(ValueError, match="registry token is empty"):
        module.read_registry_credentials(StringIO("\n"))


def test_deploy_streams_registry_token_without_persisting_it() -> None:
    source = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert "az acr login" in source
    assert "--expose-token" in source
    assert "--query accessToken" in source
    assert "--registry-token-stdin" in source
    assert "registry_token=" not in source
    assert "--registry-token " not in source