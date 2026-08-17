"""execution_health_monitor L1/L2/L3."""

from __future__ import annotations

import pytest
from obase.loop_breaker import init_breaker, reset_breaker

from omodul.execution_health_monitor import MonitorConfig, execution_health_monitor


@pytest.fixture
def breaker():
    token = init_breaker()
    try:
        yield
    finally:
        reset_breaker(token)


@pytest.mark.asyncio
async def test_uninitialized_breaker_fails(tmp_path) -> None:
    rec = await execution_health_monitor(
        MonitorConfig(),
        {"tool_name": "x", "arguments": {}},
        tmp_path,
    )
    assert rec["status"] == "failed"
    assert rec["findings"]["action"] == "halt"


@pytest.mark.asyncio
async def test_l1_consecutive_errors(breaker, tmp_path) -> None:
    cfg = MonitorConfig(max_consecutive_errors=3)
    last = None
    for _ in range(3):
        last = await execution_health_monitor(
            cfg, {"tool_name": "x", "arguments": {}, "is_error": True}, tmp_path
        )
    assert last is not None
    assert last["findings"]["action"] == "intervene"
    assert "SYSTEM SHIELD" in last["findings"]["intervention_prompt"]
    assert any(ev["type"] == "L1_Breaker_Triggered" for ev in last["decision_events"])


@pytest.mark.asyncio
async def test_l2_trajectory_loop(breaker, tmp_path) -> None:
    cfg = MonitorConfig(max_consecutive_errors=99)
    last = None
    for _ in range(4):
        last = await execution_health_monitor(
            cfg, {"tool_name": "read", "arguments": {"p": "a"}}, tmp_path
        )
    assert last["findings"]["action"] == "intervene"
    assert any(ev["type"] == "L2_Breaker_Triggered" for ev in last["decision_events"])


@pytest.mark.asyncio
async def test_l3_max_steps(breaker, tmp_path) -> None:
    cfg = MonitorConfig(max_steps_per_turn=2, max_consecutive_errors=99)
    await execution_health_monitor(cfg, {"tool_name": "a", "arguments": {"i": 1}}, tmp_path)
    await execution_health_monitor(cfg, {"tool_name": "b", "arguments": {"i": 2}}, tmp_path)
    rec = await execution_health_monitor(cfg, {"tool_name": "c", "arguments": {"i": 3}}, tmp_path)
    assert rec["findings"]["action"] == "halt"
    assert any(ev["type"] == "L3_Breaker_Triggered" for ev in rec["decision_events"])
