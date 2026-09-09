import pytest
from eda_sandbox.contracts import ExecutionRequest
from eda_sandbox.settings import execution_environment
from pydantic import ValidationError


def test_execution_schema_rejects_shell_and_paths() -> None:
    with pytest.raises(ValidationError):
        ExecutionRequest.model_validate({"runtime": "bash", "command": "id", "sourceFileId": "file_12345678"})
    with pytest.raises(ValidationError):
        ExecutionRequest.model_validate({"runtime": "python", "path": "/etc/passwd", "sourceFileId": "file_12345678"})


def test_environment_allowlist_excludes_credentials(monkeypatch) -> None:
    monkeypatch.setenv("AZURE_CLIENT_ID", "must-not-flow")
    monkeypatch.setenv("SECRET_VALUE", "must-not-flow")

    env = execution_environment({"EDA_RANDOM_SEED": "42"})

    assert env == {
        "HOME": "/workspace/task/home",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "MPLCONFIGDIR": "/workspace/task/.matplotlib",
        "NODE_PATH": "/opt/eda/node_modules",
        "NUMEXPR_NUM_THREADS": "1",
        "OMP_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "PUPPETEER_EXECUTABLE_PATH": "/usr/bin/chromium",
        "PYTHONHASHSEED": "0",
        "TZ": "UTC",
        "EDA_RANDOM_SEED": "42",
    }
