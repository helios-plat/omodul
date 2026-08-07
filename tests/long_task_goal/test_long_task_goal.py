"""omodul.long_task_goal 行为测试矩阵。

覆盖: goal 生命周期 / 跨实例重建 (连续两天的根基) / todo 状态机 /
operator gate 人工审批 / auto gate 自动放行 / 尾部截断检测 /
evidence 绑定 / handoff / 配额投影 (与 obase.QuotaTracker 联测) /
增量 apply seq 校验 / 非法事件拒绝。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# 开发接线: 3O 主库是 src 布局 (platform/3O/<lib>/<lib>/), 注入仓库路径
# parents[3] = platform/3O (test → long_task_goal → tests → omodul → 3O)
_THREE_O = Path(__file__).resolve().parents[3]
for _lib in ("obase", "oprim", "omodul", "oskill", "oservi"):
    _p = str(_THREE_O / _lib)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from obase.exceptions import BudgetExceeded  # noqa: E402
from obase.loop_event_store import (  # noqa: E402
    AppendOnlyEventStore,
    QuotaTracker,
)

from omodul.long_task_goal import (  # noqa: E402
    EVENT_GATE_RESOLVED,
    Goal,
    GoalKernel,
    GoalKernelError,
    Todo,
)

# ---------------------------------------------------------------------------
# goal 生命周期 + 跨实例重建
# ---------------------------------------------------------------------------


async def test_goal_lifecycle_and_rebuild(tmp_path):
    store = AppendOnlyEventStore(tmp_path / "g1.jsonl")
    kernel = GoalKernel(store, goal_id="g1")
    await kernel.add_goal("重构结算模块", budget_usd=5.0, meta={"owner": "veya"})
    await kernel.update_todo("t1", title="拆服务", status="done")
    await kernel.update_todo("t2", title="写测试")
    assert isinstance(kernel.goal, Goal)
    assert kernel.goal.title == "重构结算模块"
    assert kernel.goal.quota.budget_usd == 5.0
    assert kernel.goal.todos["t1"].status == "done"

    # 跨天恢复: 全新实例从事件流重建, 状态一致
    fresh = GoalKernel(store, goal_id="g1").rebuild()
    assert fresh.goal.title == "重构结算模块"
    assert set(fresh.goal.todos) == {"t1", "t2"}
    assert fresh.goal.todos["t1"].status == "done"
    assert fresh.goal.todos["t2"].status == "open"
    assert fresh.last_seq == kernel.last_seq
    assert fresh.check_integrity().ok
    # 重建后仍可续写
    await fresh.update_todo("t2", status="done")
    assert fresh.goal.is_complete()


async def test_rebuild_identical_across_instances(tmp_path):
    store = AppendOnlyEventStore(tmp_path / "g.jsonl")
    kernel = GoalKernel(store, goal_id="g1")
    await kernel.add_goal("长程重构")
    for i in range(10):
        await kernel.update_todo(f"t{i}", title=f"任务 {i}", status="done" if i % 2 else "open")
    await kernel.require_gate("operator", waiting_on=["t0"], gate_id="gate-a")
    await kernel.append_evidence("test_pass", {"n": i}, todo_id="t1")

    fresh = GoalKernel(store, goal_id="g1").rebuild()
    assert len(fresh.goal.todos) == 10
    assert fresh.goal.todos["t9"].status == "done"  # 9 % 2 == 1
    assert fresh.goal.gates["gate-a"].status == "open"
    assert len(fresh.goal.evidence) == 1
    assert fresh.check_integrity().ok
    assert fresh.last_seq == kernel.last_seq


async def test_duplicate_goal_rejected(tmp_path):
    store = AppendOnlyEventStore(tmp_path / "g.jsonl")
    kernel = GoalKernel(store, goal_id="g1")
    await kernel.add_goal("a")
    with pytest.raises(GoalKernelError):
        await kernel.add_goal("b")


async def test_unknown_event_type_rejected(tmp_path):
    store = AppendOnlyEventStore(tmp_path / "g.jsonl")
    kernel = GoalKernel(store, goal_id="g1")
    await kernel.add_goal("a")
    row = await store.append("mystery_event", {"x": 1})
    with pytest.raises(GoalKernelError):
        kernel.apply(row)


# ---------------------------------------------------------------------------
# todo 状态机
# ---------------------------------------------------------------------------


async def test_todo_status_machine(tmp_path):
    store = AppendOnlyEventStore(tmp_path / "g.jsonl")
    kernel = GoalKernel(store, goal_id="g1")
    await kernel.add_goal("g")
    await kernel.update_todo("t1", title="写实现")
    assert kernel.goal.todos["t1"].status == "open"
    await kernel.update_todo("t1", status="blocked")
    assert kernel.goal.todos["t1"].status == "blocked"
    await kernel.update_todo("t1", status="deferred")
    assert kernel.goal.todos["t1"].status == "deferred"
    await kernel.update_todo("t1", status="done", note="全部通过")
    assert kernel.goal.todos["t1"].note == "全部通过"
    with pytest.raises(GoalKernelError):
        await kernel.update_todo("t2", status="invalid")


async def test_next_action_returns_first_open(tmp_path):
    store = AppendOnlyEventStore(tmp_path / "g.jsonl")
    kernel = GoalKernel(store, goal_id="g1")
    await kernel.add_goal("g")
    await kernel.update_todo("t1", status="done")
    await kernel.update_todo("t2")
    await kernel.update_todo("t3")
    action = kernel.next_action()
    assert isinstance(action, Todo)
    assert action.id == "t2"  # 按创建顺序第一个 open


# ---------------------------------------------------------------------------
# gate 状态机
# ---------------------------------------------------------------------------


async def test_operator_gate_requires_manual_resolve(tmp_path):
    store = AppendOnlyEventStore(tmp_path / "g.jsonl")
    kernel = GoalKernel(store, goal_id="g1")
    await kernel.add_goal("g")
    await kernel.update_todo("t1", status="done")
    await kernel.require_gate("operator", waiting_on=["t1"], gate_id="gate-ops")
    gate = kernel.goal.gates["gate-ops"]
    assert gate.status == "open"  # waiting 已 done, 但 operator gate 仍要人工
    assert kernel.goal.is_complete() is False
    await kernel.resolve_gate("gate-ops", approved=True, by="human")
    assert kernel.goal.gates["gate-ops"].status == "resolved"
    assert kernel.goal.gates["gate-ops"].approved is True
    assert kernel.goal.is_complete() is True
    # 重复放行报错
    with pytest.raises(GoalKernelError):
        await kernel.resolve_gate("gate-ops", approved=True, by="human")
    # 拒绝路径
    await kernel.require_gate("operator", waiting_on=["t1"], gate_id="gate-2")
    await kernel.resolve_gate("gate-2", approved=False, by="human")
    assert kernel.goal.gates["gate-2"].approved is False


async def test_auto_gate_resolves_when_todos_done(tmp_path):
    store = AppendOnlyEventStore(tmp_path / "g.jsonl")
    kernel = GoalKernel(store, goal_id="g1")
    await kernel.add_goal("g")
    await kernel.require_gate("auto", waiting_on=["t1", "t2"], gate_id="gate-auto")
    assert kernel.goal.pending_gates()  # 未完成 → open
    await kernel.update_todo("t1", status="done")
    assert kernel.goal.pending_gates()  # 还有 t2
    await kernel.update_todo("t2", status="done")  # 触发自动放行
    assert not kernel.goal.pending_gates()
    assert kernel.goal.gates["gate-auto"].status == "resolved"
    # 自动放行必须走事件流 (by=auto), 保证真相源一致
    resolved = [e for e in store.replay() if e["type"] == EVENT_GATE_RESOLVED]
    assert resolved and resolved[-1]["payload"]["by"] == "auto"
    # 跨实例重建后 gate 已放行
    fresh = GoalKernel(store, goal_id="g1").rebuild()
    assert fresh.goal.gates["gate-auto"].status == "resolved"


async def test_invalid_gate_kind_rejected(tmp_path):
    store = AppendOnlyEventStore(tmp_path / "g.jsonl")
    kernel = GoalKernel(store, goal_id="g1")
    await kernel.add_goal("g")
    with pytest.raises(GoalKernelError):
        await kernel.require_gate("weird", waiting_on=[], gate_id="g-x")


# ---------------------------------------------------------------------------
# 完整性: 尾部截断检测 (verify 保内部, 期望 seq 保完整)
# ---------------------------------------------------------------------------


async def test_tail_truncation_detected(tmp_path):
    path = tmp_path / "g.jsonl"
    store = AppendOnlyEventStore(path)
    kernel = GoalKernel(store, goal_id="g1")
    await kernel.add_goal("g")
    await kernel.update_todo("t1")
    await kernel.update_todo("t2")
    kernel.rebuild()  # last_seq = 3 (期望基准)
    # 截断最后一行
    lines = path.read_text().strip().splitlines()[:-1]
    path.write_text("\n".join(lines) + "\n")
    result = kernel.check_integrity()
    assert not result.ok
    assert "tail truncated" in result.error
    assert result.expected_last_seq == 3
    assert result.actual_last_seq == 2


async def test_integrity_ok_on_full_stream(tmp_path):
    store = AppendOnlyEventStore(tmp_path / "g.jsonl")
    kernel = GoalKernel(store, goal_id="g1")
    await kernel.add_goal("g")
    await kernel.update_todo("t1")
    result = kernel.check_integrity()
    assert result.ok
    assert result.actual_last_seq == 2


# ---------------------------------------------------------------------------
# evidence / handoff
# ---------------------------------------------------------------------------


async def test_evidence_bound_to_todo(tmp_path):
    store = AppendOnlyEventStore(tmp_path / "g.jsonl")
    kernel = GoalKernel(store, goal_id="g1")
    await kernel.add_goal("g")
    await kernel.update_todo("t1")
    ev_id = await kernel.append_evidence("test_pass", {"suite": "unit", "n": 12}, todo_id="t1")
    assert kernel.goal.todos["t1"].evidence_refs == [ev_id]
    assert kernel.goal.evidence[0].kind == "test_pass"
    fresh = GoalKernel(store, goal_id="g1").rebuild()
    assert fresh.goal.todos["t1"].evidence_refs == [ev_id]
    assert fresh.goal.evidence[0].detail["n"] == 12


async def test_handoff_recorded(tmp_path):
    store = AppendOnlyEventStore(tmp_path / "g.jsonl")
    kernel = GoalKernel(store, goal_id="g1")
    await kernel.add_goal("g")
    await kernel.update_todo("t1", status="done")
    await kernel.record_handoff("build", "t1 完成, 下一步拆服务")
    fresh = GoalKernel(store, goal_id="g1").rebuild()
    assert fresh.goal.handoffs[0].to == "build"
    assert "下一步" in fresh.goal.handoffs[0].summary


# ---------------------------------------------------------------------------
# 配额投影 (与 obase.QuotaTracker 联测)
# ---------------------------------------------------------------------------


async def test_quota_events_projected(tmp_path):
    store = AppendOnlyEventStore(tmp_path / "g.jsonl")
    kernel = GoalKernel(store, goal_id="g1")
    await kernel.add_goal("g", budget_usd=2.0)
    quota = QuotaTracker(budget_usd=2.0, goal_id="g1", store=store)
    await quota.record_usage(0.5)
    fresh = GoalKernel(store, goal_id="g1").rebuild()
    assert fresh.goal.quota.spent_usd == pytest.approx(0.5)
    assert fresh.goal.quota.remaining_usd == pytest.approx(1.5)
    assert fresh.goal.quota.paused is False
    # 超支 → 投影 paused
    with pytest.raises(BudgetExceeded):
        await quota.record_usage(9.0)
    fresh2 = GoalKernel(store, goal_id="g1").rebuild()
    assert fresh2.goal.quota.paused is True


# ---------------------------------------------------------------------------
# 增量 apply
# ---------------------------------------------------------------------------


async def test_apply_rejects_seq_gap(tmp_path):
    store = AppendOnlyEventStore(tmp_path / "g.jsonl")
    kernel = GoalKernel(store, goal_id="g1")
    await kernel.add_goal("g")  # apply seq 1 → last_seq=1
    row = await store.append("todo_updated", {"todo_id": "t1"})  # seq 2, 未 apply
    fake = dict(row)
    fake["seq"] = 3  # 跳过 seq 2
    with pytest.raises(GoalKernelError):
        kernel.apply(fake)


async def test_apply_incremental_then_rebuild_same(tmp_path):
    store = AppendOnlyEventStore(tmp_path / "g.jsonl")
    kernel = GoalKernel(store, goal_id="g1")
    await kernel.add_goal("g")
    await kernel.update_todo("t1", status="done")  # 增量 apply
    fresh = GoalKernel(store, goal_id="g1").rebuild()  # 全量重放
    assert fresh.goal.todos["t1"].status == "done"
    assert fresh.last_seq == kernel.last_seq == 2
