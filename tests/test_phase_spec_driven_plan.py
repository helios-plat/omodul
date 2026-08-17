"""phase_spec_driven_plan + verify + constitution monitor."""

from __future__ import annotations

import pytest
from obase.loop_breaker import init_breaker, reset_breaker

from omodul.execution_health_monitor import MonitorConfig, execution_health_monitor
from omodul.phase_spec_driven_plan import SpecPlanConfig, phase_spec_driven_plan
from omodul.phase_verify_leaf_task import phase_verify_leaf_task


def _speckit(root) -> None:
    spec = root / ".speckit"
    spec.mkdir()
    (spec / "constitution.md").write_text("Do not use axios\nMust use fetch\n", encoding="utf-8")
    (spec / "tasks.md").write_text(
        "- [ ] T1 Init\n  Acceptance: init done\n- [ ] T2 Fetch client\n  Depends: T1\n",
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_plan_compiles(tmp_path) -> None:
    _speckit(tmp_path)
    rec = await phase_spec_driven_plan(
        SpecPlanConfig(goal_id="g1", project_root=tmp_path),
        {},
        tmp_path / "out",
    )
    assert rec["status"] == "completed"
    assert rec["findings"]["constitution"].startswith("Do not use")
    assert [t["id"] for t in rec["findings"]["tasks"]] == ["T1", "T2"]


@pytest.mark.asyncio
async def test_plan_missing_artifacts(tmp_path) -> None:
    rec = await phase_spec_driven_plan(
        {"goal_id": "g1", "project_root": str(tmp_path)},
        {},
        tmp_path / "out",
    )
    assert rec["status"] == "failed"
    assert rec["error"]["type"] == "MissingSpecKit"


@pytest.mark.asyncio
async def test_verify_missing_acceptance(tmp_path) -> None:
    rec = await phase_verify_leaf_task(
        {},
        {"execution_log": "hello", "acceptance": ["init done"]},
        tmp_path,
    )
    assert rec["findings"]["passed"] is False


@pytest.mark.asyncio
async def test_monitor_constitution(tmp_path) -> None:
    token = init_breaker()
    try:
        rec = await execution_health_monitor(
            MonitorConfig(),
            {
                "tool_name": "leaf",
                "execution_log": "npm i axios",
                "constitution": "Do not use axios",
            },
            tmp_path,
        )
        assert rec["findings"]["action"] == "intervene"
        assert "宪法" in rec["findings"]["intervention_prompt"]
    finally:
        reset_breaker(token)
