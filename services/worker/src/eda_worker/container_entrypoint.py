from __future__ import annotations

import os
import sys
from collections.abc import Sequence
from pathlib import Path

_RUNTIME_UID = 10001
_RUNTIME_GID = 10001
_RUNTIME_HOME = "/app"
_ROOT_HOME = "/root"


def _runtime_home() -> Path:
    configured = os.environ.get("HOME")
    if not configured or configured == _ROOT_HOME:
        configured = _RUNTIME_HOME
        os.environ["HOME"] = configured
    return Path(configured)


def _state_root(home: Path) -> Path:
    configured = os.environ.get("AGENTSERVER_STATE_ROOT")
    return Path(configured) if configured else home / ".agentserver"


def _prepare_state_root(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise RuntimeError("agent server state root must be a directory, not a symlink")
    path.chmod(0o700)
    os.chown(path, _RUNTIME_UID, _RUNTIME_GID, follow_symlinks=False)


def _drop_privileges() -> None:
    os.setgroups([])
    os.setgid(_RUNTIME_GID)
    os.setuid(_RUNTIME_UID)


def main(arguments: Sequence[str] | None = None) -> None:
    command = list(sys.argv[1:] if arguments is None else arguments)
    effective_uid = os.geteuid()
    if effective_uid == 0:
        os.umask(0o077)
        _prepare_state_root(_state_root(_runtime_home()))
        _drop_privileges()
    elif effective_uid != _RUNTIME_UID:
        raise PermissionError(f"worker image must start as root or UID {_RUNTIME_UID}")
    os.execv(  # noqa: S606 - replacing PID 1 with the image-owned CLI is intentional.
        "/usr/local/bin/eda-worker",
        ["eda-worker", *command],
    )


if __name__ == "__main__":
    main()
