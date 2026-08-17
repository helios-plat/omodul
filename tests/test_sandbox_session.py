"""sandbox_session always destroys; failed create does not lie."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


def _load():
    path = Path(__file__).resolve().parents[1] / "omodul" / "sandbox_session.py"
    spec = importlib.util.spec_from_file_location("sandbox_session_under_test", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def sess_mod():
    return _load()


def test_memory_session_closes_even_on_error(sess_mod) -> None:
    from oprim._sandbox_env import sandbox_exec

    session = sess_mod.sandbox_session("memory_test")
    assert session.ok is True
    sid = session.sandbox_id
    try:
        session.put_file("a.py", "x = 1\n")
        ran = session.exec([sys.executable, "-c", "print(open('a.py').read())"])
        assert ran["ok"] is True
        raise RuntimeError("boom")
    except RuntimeError:
        session.close()
    dead = sandbox_exec(sid, [sys.executable, "-c", "print(1)"])
    assert dead["ok"] is False


def test_scope_destroys(sess_mod) -> None:
    from oprim._sandbox_env import sandbox_get_file

    with sess_mod.sandbox_scope("memory_test") as session:
        session.put_file("z.txt", "z")
        sid = session.sandbox_id
    rec = sandbox_get_file(sid, "z.txt")
    assert rec["ok"] is False


def test_eval_in_sandbox_local_pytest(sess_mod) -> None:
    rec = sess_mod.eval_in_sandbox(
        files={"test_ok.py": "def test_ok():\n    assert True\n"},
        purpose="pytest_local",
        timeout_s=60,
    )
    assert rec.get("isolation") == "process"
    assert rec.get("passed") is True


def test_session_forwards_pty_and_memory_refuses(sess_mod) -> None:
    session = sess_mod.sandbox_session("memory_test")
    rec = session.exec([sys.executable, "-c", "print(1)"], pty=True)
    assert rec["ok"] is False
    assert "PTY" in rec["error"]
    session.close()


def test_docker_purpose_fails_honestly_without_runtime(sess_mod) -> None:
    from oprim._sandbox_backends import docker_available

    if docker_available():
        pytest.skip("docker is present; honesty-fail path not exercised")
    session = sess_mod.sandbox_session("pytest_eval")
    assert session.ok is False
    assert "docker" in session.error
