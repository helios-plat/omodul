"""G0 intent triage."""

from __future__ import annotations

import pytest

from omodul.phase_intent_triage import phase_intent_triage


async def _plan_brief(*, messages, max_tokens):
    return {
        "ok": True,
        "action": "plan",
        "interpretation": "add foo() to util.py",
        "in_scope_files": ["util.py"],
        "out_of_scope_files": ["tests/"],
        "acceptance_draft": ["git diff adds def foo"],
        "questions": [],
        "reasons": ["clear"],
    }


async def _ask_brief(*, messages, max_tokens):
    return {
        "ok": True,
        "action": "ask",
        "interpretation": "",
        "questions": ["要改哪个文件？ auth.py 还是 session.py？"],
        "reasons": ["ambiguous file"],
    }


async def _bad_plan(*, messages, max_tokens):
    return {"ok": True, "action": "plan", "interpretation": "", "acceptance_draft": []}


@pytest.mark.asyncio
async def test_intent_plan(tmp_path) -> None:
    rec = await phase_intent_triage(
        {"goal_id": "g1"},
        {
            "goal": "add foo",
            "snapshot": {"git_diff": "", "ast_summary": {}, "active_files": []},
            "llm_caller": _plan_brief,
        },
        tmp_path,
    )
    assert rec["status"] == "completed"
    assert rec["findings"]["brief"]["action"] == "plan"
    assert "util.py" in rec["findings"]["brief"]["in_scope_files"]


@pytest.mark.asyncio
async def test_intent_ask_is_completed_not_failed(tmp_path) -> None:
    rec = await phase_intent_triage(
        {"goal_id": "g1"},
        {
            "goal": "fix it",
            "snapshot": {"git_diff": "", "ast_summary": {}, "active_files": []},
            "llm_caller": _ask_brief,
        },
        tmp_path,
    )
    assert rec["status"] == "completed"
    assert rec["findings"]["brief"]["action"] == "ask"
    assert rec["findings"]["brief"]["questions"]


@pytest.mark.asyncio
async def test_intent_invalid_plan_fails(tmp_path) -> None:
    rec = await phase_intent_triage(
        {"goal_id": "g1"},
        {
            "goal": "x",
            "snapshot": {"git_diff": "", "ast_summary": {}, "active_files": []},
            "llm_caller": _bad_plan,
        },
        tmp_path,
    )
    assert rec["status"] == "failed"
    assert rec["error"]["type"] == "InvalidIntent"
