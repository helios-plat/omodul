"""implicit_feedback_processor routes style / knowledge / logic."""

from __future__ import annotations

import subprocess

import pytest
from obase.graph_store.models import GraphDBPool

from omodul.implicit_feedback_processor import FeedbackConfig, implicit_feedback_processor


def _write(path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def _git(repo, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.mark.asyncio
async def test_style_rename_ignored(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo / "a.py", "def old(x):\n    return x\n")
    # worktree is both v0 and v1 (same file) — no meaningful change
    rec = await implicit_feedback_processor(
        FeedbackConfig(repo_path=repo, file_path="a.py"),
        {},
        tmp_path / "out",
    )
    assert rec["status"] == "completed"
    assert rec["findings"]["action"] == "ignored_style_noise"


@pytest.mark.asyncio
async def test_knowledge_updates_graph(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo / "v0.py", "MAX = 1\n")
    _write(repo / "v1.py", "MAX = 2\n")
    # two files: processor compares the same path at two commits.
    # without git, both commits empty → same worktree file.
    # simulate by running processor on v1 after we only have one path:
    _write(repo / "a.py", "MAX = 2\n")
    # Force a knowledge diff by feeding extract via two copies is hard
    # without git; instead write a.py then compare against a committed v0.
    import subprocess

    _git(repo, "init")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    _write(repo / "a.py", "MAX = 1\n")
    _git(repo, "add", "a.py")
    _git(repo, "commit", "-m", "v0")
    v0 = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    _write(repo / "a.py", "MAX = 2\n")
    pool = GraphDBPool()
    rec = await implicit_feedback_processor(
        FeedbackConfig(repo_path=repo, file_path="a.py", v0_commit=v0, v1_commit=""),
        {"graph_pool": pool, "entity_id": "a.py"},
        tmp_path / "out",
    )
    assert rec["status"] == "completed"
    assert rec["findings"]["action"] == "knowledge_graph_updated"
    assert pool.find_active("a.py", predicate="ast_delta") is not None


@pytest.mark.asyncio
async def test_logic_appends_experience(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    import subprocess

    _git(repo, "init")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "a.py").write_text("def f(x):\n    return x\n", encoding="utf-8")
    _git(repo, "add", "a.py")
    _git(repo, "commit", "-m", "v0")
    v0 = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    (repo / "a.py").write_text(
        "def f(x):\n    if x:\n        return x\n    return 0\n",
        encoding="utf-8",
    )
    pool = GraphDBPool()
    rec = await implicit_feedback_processor(
        FeedbackConfig(repo_path=repo, file_path="a.py", v0_commit=v0, v1_commit=""),
        {"graph_pool": pool},
        tmp_path / "out",
    )
    assert rec["findings"]["action"] == "experience_pool_appended"
    assert pool.experiences
