from __future__ import annotations

import os
import sys

import uvicorn

from .settings import IMPORTS, OUTPUTS, SOURCES, TEMP, WORKSPACE


def prepare_workspace() -> None:
    if os.getuid() == 0 or os.getgid() == 0:
        raise RuntimeError("sandbox must not run as root")
    for directory in (WORKSPACE, IMPORTS, SOURCES, OUTPUTS, TEMP, WORKSPACE / "home"):
        directory.mkdir(parents=True, exist_ok=True)
    probe = WORKSPACE / ".write-probe"
    try:
        probe.write_bytes(b"")
    finally:
        probe.unlink(missing_ok=True)
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"


def main() -> None:
    try:
        prepare_workspace()
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from error
    uvicorn.run("eda_sandbox.app:app", host="0.0.0.0", port=8080, workers=1)  # noqa: S104


if __name__ == "__main__":
    main()
