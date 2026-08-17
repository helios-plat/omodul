"""run_harness uses the unified sandbox; does not delete caller workspace."""

from __future__ import annotations

import sys

from omodul.run_harness import run_harness


def test_master_rejected() -> None:
    rec = run_harness("master", "hi")
    assert rec["ok"] is False
    assert "not a harness" in rec["error"]


def test_argv_override_in_memory(tmp_path) -> None:
    marker = tmp_path / "keep.txt"
    marker.write_text("stay\n", encoding="utf-8")
    rec = run_harness(
        "pi",
        "ignored",
        workspace=str(tmp_path),
        purpose="memory_test",
        argv=[sys.executable, "-c", "print(open('keep.txt').read())"],
        timeout_s=15,
    )
    assert rec["ok"] is True
    assert "stay" in rec["output"]
    assert marker.read_text(encoding="utf-8") == "stay\n"
