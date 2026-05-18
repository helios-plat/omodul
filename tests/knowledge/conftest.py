"""Shared fixtures for omodul.knowledge tests."""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture()
def stratum_home(tmp_path, monkeypatch):
    home = tmp_path / "stratum"
    home.mkdir()
    monkeypatch.setenv("STRATUM_HOME", str(home))
    import oprim._config as _cfg_mod
    _cfg_mod._store["STRATUM_HOME"] = str(home)
    yield home
    _cfg_mod._store.pop("STRATUM_HOME", None)


@pytest.fixture()
def simple_md(tmp_path: Path) -> Path:
    p = tmp_path / "test_note.md"
    p.write_text("# Test Note\n\nThis is a test markdown note.\n\nSome content here.\n")
    return p


@pytest.fixture()
def inbox_with_files(stratum_home: Path, tmp_path: Path) -> Path:
    """Inbox dir with two .txt files and one hidden file."""
    inbox = stratum_home / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "note_a.md").write_text("# Note A\nContent of note A.")
    (inbox / "note_b.md").write_text("# Note B\nContent of note B.")
    (inbox / ".hidden").write_text("hidden")
    return inbox
