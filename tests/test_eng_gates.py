"""omodul.project_eng_gates — S2 fail is total fail; gui_required calls S5; no push."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_eng_gates():
    # Load the module file directly so this unit test does not pay for the
    # eager omodul package import (commerce + notify + obase).
    path = Path(__file__).resolve().parents[1] / "omodul" / "eng_gates.py"
    spec = importlib.util.spec_from_file_location("eng_gates_under_test", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.project_eng_gates


project_eng_gates = _load_eng_gates()


def _skills(**overrides):
    base = {
        "pre_push_checks": lambda *a, **k: {
            "ok": True,
            "files": ["foo.py"],
            "reason": "",
            "skipped": False,
        },
        "code_review": lambda *a, **k: {"ok": True, "verdict": "pass", "findings": []},
        "find_simplifications": lambda *a, **k: {
            "ok": True,
            "proposals": [],
            "proposal_paths": [],
            "written_business_source": [],
        },
        "archive_agent_notes": lambda *a, **k: {
            "ok": True,
            "promoted": [],
            "suppressed": [],
            "written_business_source": [],
        },
        "record_browser_gif": lambda *a, **k: {"ok": True, "path": "/tmp/x.png", "reason": ""},
    }
    base.update(overrides)
    return base


def test_pre_merge_s2_fail_is_total_fail_no_push(tmp_path: Path) -> None:
    called = []

    def s2(*a, **k):
        called.append("s2")
        return {"ok": False, "files": ["foo.py"], "reason": "pytest failed"}

    def s5(*a, **k):
        called.append("s5")
        return {"ok": True, "path": "x", "reason": ""}

    rec = project_eng_gates(
        str(tmp_path),
        profile="pre_merge",
        gui_required=False,
        skills=_skills(pre_push_checks=s2, record_browser_gif=s5),
    )
    assert rec["ok"] is False
    assert rec["pushed"] is False
    assert "s2" in called
    names = [s["name"] for s in rec["steps"]]
    assert names[0] == "pre_push_checks"
    assert "record_browser_gif" not in names


def test_gui_required_calls_s5(tmp_path: Path) -> None:
    called = []

    def s5(*a, **k):
        called.append("s5")
        return {"ok": True, "path": "clip.png", "reason": ""}

    rec = project_eng_gates(
        str(tmp_path),
        profile="pre_merge",
        gui_required=True,
        url="http://127.0.0.1:9/",
        skills=_skills(record_browser_gif=s5),
    )
    assert rec["ok"] is True
    assert called == ["s5"]
    assert rec["steps"][-1]["name"] == "record_browser_gif"


def test_hygiene_s3_does_not_write_business_source(tmp_path: Path) -> None:
    rec = project_eng_gates(
        str(tmp_path),
        profile="hygiene",
        skills=_skills(
            find_simplifications=lambda *a, **k: {
                "ok": True,
                "proposals": [{"title": "dedup", "kind": "duplication"}],
                "proposal_paths": [str(tmp_path / ".veya-project/engineering/proposals/x.md")],
                "written_business_source": [],
            }
        ),
    )
    assert rec["ok"] is True
    assert rec["steps"][0]["name"] == "find_simplifications"
    assert rec["steps"][1]["name"] == "archive_agent_notes"
    assert rec["steps"][0]["result"]["written_business_source"] == []


def test_unknown_profile_fails(tmp_path: Path) -> None:
    rec = project_eng_gates(str(tmp_path), profile="ship_it")
    assert rec["ok"] is False
    assert "unknown profile" in rec["reason"]


def test_gui_profile_only_s5(tmp_path: Path) -> None:
    rec = project_eng_gates(
        str(tmp_path),
        profile="gui",
        skills=_skills(
            record_browser_gif=lambda *a, **k: {
                "ok": False,
                "reason": "playwright not installed; will not fabricate a clip",
            }
        ),
    )
    assert rec["ok"] is False
    assert [s["name"] for s in rec["steps"]] == ["record_browser_gif"]
    assert "playwright" in rec["reason"]
