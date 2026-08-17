"""G1 closed-loop plan + G2 evidence verify."""

from __future__ import annotations

import pytest

from omodul.phase_closed_loop_plan import phase_closed_loop_plan
from omodul.phase_evidence_verify import phase_evidence_verify


async def _plan_ok(*, messages, max_tokens):
    return {
        "ok": True,
        "tasks": [
            {
                "id": "T1",
                "title": "Add helper",
                "files": ["util.py"],
                "logic": "add foo()",
                "forbidden": ["do not touch tests"],
                "instruction": "Modify util.py; add foo(); do not touch tests.",
                "acceptance": ["git diff adds def foo"],
                "depends_on": [],
                "assignee": "hicode",
            }
        ],
    }


async def _plan_cycle(*, messages, max_tokens):
    return {
        "ok": True,
        "tasks": [
            {
                "id": "T1",
                "title": "A",
                "instruction": "A",
                "acceptance": ["a"],
                "depends_on": ["T2"],
            },
            {
                "id": "T2",
                "title": "B",
                "instruction": "B",
                "acceptance": ["b"],
                "depends_on": ["T1"],
            },
        ],
    }


async def _verify_fail(*, messages, max_tokens):
    return {"ok": True, "passed": False, "reasoning": "diff missing foo"}


async def _verify_pass(*, messages, max_tokens):
    return {"ok": True, "passed": True, "reasoning": "foo present"}


@pytest.mark.asyncio
async def test_plan_ok(tmp_path) -> None:
    rec = await phase_closed_loop_plan(
        {"goal_id": "g1", "default_assignee": "hicode"},
        {
            "goal": "add foo",
            "snapshot": {"git_diff": "", "ast_summary": {}, "active_files": []},
            "llm_caller": _plan_ok,
        },
        tmp_path,
    )
    assert rec["status"] == "completed"
    tasks = rec["findings"]["graph"]["tasks"]
    assert tasks[0]["id"] == "T1"
    assert tasks[0]["assignee"] == "hicode"
    assert tasks[0]["files"] == ["util.py"]


async def _plan_no_contract(*, messages, max_tokens):
    return {
        "ok": True,
        "tasks": [
            {
                "id": "T1",
                "title": "Vague",
                "instruction": "do stuff",
                "acceptance": ["done"],
                "depends_on": [],
            }
        ],
    }


@pytest.mark.asyncio
async def test_plan_rejects_missing_contract(tmp_path) -> None:
    rec = await phase_closed_loop_plan(
        {"goal_id": "g1", "default_assignee": "hicode"},
        {
            "goal": "x",
            "snapshot": {"git_diff": "", "ast_summary": {}, "active_files": []},
            "intent_brief": {
                "action": "plan",
                "interpretation": "do x",
                "acceptance_draft": ["done"],
            },
            "llm_caller": _plan_no_contract,
        },
        tmp_path,
    )
    assert rec["status"] == "failed"
    assert rec["error"]["type"] == "InvalidLeafContract"


@pytest.mark.asyncio
async def test_plan_invalid_dag(tmp_path) -> None:
    rec = await phase_closed_loop_plan(
        {"goal_id": "g1", "default_assignee": "hicode"},
        {
            "goal": "x",
            "snapshot": {"git_diff": "", "ast_summary": {}, "active_files": []},
            "llm_caller": _plan_cycle,
        },
        tmp_path,
    )
    assert rec["status"] == "failed"
    assert rec["error"]["type"] == "InvalidDAG"


@pytest.mark.asyncio
async def test_evidence_fail_has_correction(tmp_path) -> None:
    rec = await phase_evidence_verify(
        {},
        {
            "task": {"acceptance": ["git diff adds def foo"]},
            "leaf_result": {"git_diff": "diff --git a/a.py", "stdout": "ok"},
            "llm_caller": _verify_fail,
        },
        tmp_path,
    )
    assert rec["status"] == "completed"
    assert rec["findings"]["passed"] is False
    assert "RETRY FEEDBACK" in rec["findings"]["correction_instruction"]


@pytest.mark.asyncio
async def test_evidence_pass(tmp_path) -> None:
    rec = await phase_evidence_verify(
        {},
        {
            "task": {"acceptance": ["foo"]},
            "leaf_result": {"git_diff": "+def foo", "stdout": "ok"},
            "llm_caller": _verify_pass,
        },
        tmp_path,
    )
    assert rec["findings"]["passed"] is True
