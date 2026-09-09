from __future__ import annotations

from pathlib import Path

WORKSPACE = Path("/workspace/task")
IMPORTS = WORKSPACE / "inputs"
SOURCES = WORKSPACE / "sources"
OUTPUTS = WORKSPACE / "outputs"
TEMP = WORKSPACE / "tmp"
MAX_IMPORT_BYTES = 500 * 1024 * 1024
MAX_SOURCE_BYTES = 64 * 1024
MAX_OUTPUT_BYTES = 500 * 1024 * 1024
MAX_OUTPUT_FILES = 1000
MAX_STDIO_BYTES = 1 * 1024 * 1024
MAX_EXECUTION_SECONDS = 300
MAX_PROCESSES = 128
MAX_OPEN_FILES = 1024


def execution_environment(extra: dict[str, str] | None = None) -> dict[str, str]:
    environment = {
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
    }
    if extra:
        environment.update({key: value for key, value in extra.items() if key == "EDA_RANDOM_SEED"})
    return environment
