from __future__ import annotations

import importlib.util
import stat
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[3]
ENTRYPOINT = ROOT / "services/worker/src/eda_worker/container_entrypoint.py"
DOCKERFILE = ROOT / "services/worker/Dockerfile"


class ExecCalled(Exception):
    pass


def _load_entrypoint() -> ModuleType:
    assert ENTRYPOINT.is_file(), "the worker image needs a session-home bootstrap"
    specification = importlib.util.spec_from_file_location("container_entrypoint_under_test", ENTRYPOINT)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_worker_image_bootstraps_the_foundry_session_home_before_starting() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    assert "USER 10001:10001" not in dockerfile
    assert 'ENTRYPOINT ["python", "-m", "eda_worker.container_entrypoint"]' in dockerfile
    assert 'CMD ["run"]' in dockerfile


def test_root_bootstrap_prepares_private_state_then_drops_privileges(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    entrypoint = _load_entrypoint()
    session_home = tmp_path / "session"
    state_root = session_home / ".agentserver"
    calls: list[tuple[object, ...]] = []
    monkeypatch.setenv("HOME", str(session_home))
    monkeypatch.delenv("AGENTSERVER_STATE_ROOT", raising=False)
    monkeypatch.setattr(entrypoint.os, "geteuid", lambda: 0)
    monkeypatch.setattr(entrypoint.os, "umask", lambda mask: calls.append(("umask", mask)))
    monkeypatch.setattr(
        entrypoint.os,
        "chown",
        lambda path, uid, gid, *, follow_symlinks: calls.append(("chown", Path(path), uid, gid, follow_symlinks)),
    )
    monkeypatch.setattr(entrypoint.os, "setgroups", lambda groups: calls.append(("setgroups", tuple(groups))))
    monkeypatch.setattr(entrypoint.os, "setgid", lambda gid: calls.append(("setgid", gid)))
    monkeypatch.setattr(entrypoint.os, "setuid", lambda uid: calls.append(("setuid", uid)))

    def exec_worker(executable: str, arguments: list[str]) -> None:
        calls.append(("execv", executable, tuple(arguments)))
        raise ExecCalled

    monkeypatch.setattr(entrypoint.os, "execv", exec_worker)

    with pytest.raises(ExecCalled):
        entrypoint.main(["run"])

    assert state_root.is_dir()
    assert stat.S_IMODE(state_root.stat().st_mode) == 0o700
    assert calls == [
        ("umask", 0o077),
        ("chown", state_root, 10001, 10001, False),
        ("setgroups", ()),
        ("setgid", 10001),
        ("setuid", 10001),
        ("execv", "/usr/local/bin/eda-worker", ("eda-worker", "run")),
    ]


def test_bootstrap_preserves_foundry_home_but_normalizes_root_home(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    entrypoint = _load_entrypoint()
    state_root = tmp_path / "state"
    monkeypatch.setenv("HOME", "/root")
    monkeypatch.setenv("AGENTSERVER_STATE_ROOT", str(state_root))
    monkeypatch.setattr(entrypoint.os, "geteuid", lambda: 0)
    monkeypatch.setattr(entrypoint.os, "umask", lambda mask: None)
    monkeypatch.setattr(entrypoint.os, "chown", lambda path, uid, gid, *, follow_symlinks: None)
    monkeypatch.setattr(entrypoint.os, "setgroups", lambda groups: None)
    monkeypatch.setattr(entrypoint.os, "setgid", lambda gid: None)
    monkeypatch.setattr(entrypoint.os, "setuid", lambda uid: None)
    monkeypatch.setattr(
        entrypoint.os,
        "execv",
        lambda executable, arguments: (_ for _ in ()).throw(ExecCalled()),
    )

    with pytest.raises(ExecCalled):
        entrypoint.main(["cleanup", "--before", "2026-09-01T00:00:00Z"])

    assert entrypoint.os.environ["HOME"] == "/app"
    assert state_root.is_dir()
