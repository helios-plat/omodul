"""O1 多步反事实规划事务 (Phase 4) — 感知 → 规划 → 行动 → 学习 长视距闭环.

流程:
  1. 诊断:   causal_fault_diagnose (Phase 2) → 候选节点 + 观测基线
  2. 策略:   StrategyEvolver.select(threat_level) 或显式指定 → 规划参数映射
  3. 规划:   counterfactual_rollout (Phase 4) → 有限视距折扣效用最优序列
  4. 行动:   执行首步 (repair_callback) → 观测实际 ΔP
  5. 学习:   CPD 在线更新 (修复有效性收缩故障概率) + 策略价值 EMA 回写

3O layer: omodul (transaction over oprim rollout + oskill strategy).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import networkx as nx
import numpy as np

from oprim._audit_emit import AuditEmitter, JsonlSink

try:
    from obase.causal_graph_store import CausalGraphStore, get_runtime_causal_store
    from oprim._counterfactual_rollout import (
        OBSERVE_ACTION,
        RolloutAction,
        RolloutPlan,
        counterfactual_rollout,
    )
    from oprim._do_calculus_intervention import build_binary_failure_cpd_map
    from oskill._strategy_evolve import STRATEGY_NAMES, StrategyEvolver
    from omodul.causal_fault_diagnose import (
        CausalDiagnosisReport,
        causal_fault_diagnose,
    )
except ImportError:  # pragma: no cover - exercised only in minimal envs
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root))
    from obase.causal_graph_store import CausalGraphStore, get_runtime_causal_store
    from oprim._counterfactual_rollout import (
        OBSERVE_ACTION,
        RolloutAction,
        RolloutPlan,
        counterfactual_rollout,
    )
    from oprim._do_calculus_intervention import build_binary_failure_cpd_map
    from oskill._strategy_evolve import STRATEGY_NAMES, StrategyEvolver
    from omodul.causal_fault_diagnose import (
        CausalDiagnosisReport,
        causal_fault_diagnose,
    )


@dataclass
class ExecutionResult:
    node: str
    actual_delta_p: float
    cost: float
    reward: float
    p_fault_before: float
    p_fault_after: float


@dataclass
class MultiStepPlanReport:
    failure_context: str
    diagnosis: CausalDiagnosisReport
    strategy: str
    threat_level: float
    plan: RolloutPlan
    executed: bool
    execution: Optional[ExecutionResult] = None
    cpd_updated: List[str] = field(default_factory=list)
    strategy_value_after: float = 0.0
    recommended_actions: List[str] = field(default_factory=list)
    audit_trace_id: Optional[str] = None


def update_cpd_from_repair(
    cpd_map: Dict[str, Any],
    node: str,
    effectiveness: float,
) -> Dict[str, Any]:
    """在线更新: 把修复有效性折入节点 CPD 的故障概率 (浅拷贝, 不污染原图).

    effectiveness ∈ [0, 1] — 修复后该节点故障概率的收缩比例。
    """
    cpd = cpd_map.get(node)
    if cpd is None or effectiveness <= 0.0:
        return cpd_map
    import copy

    new_map = copy.deepcopy(cpd_map)
    new_cpd = new_map[node]
    values = np.asarray(new_cpd.values, dtype=float).copy()
    # values shape (card, n_cols): 行 0 = ok, 行 1 = fault
    if values.shape[0] >= 2:
        values[1] *= max(0.0, 1.0 - float(effectiveness))
        # 重新归一化每列
        col_sums = values.sum(axis=0, keepdims=True)
        col_sums[col_sums <= 0] = 1.0
        values = values / col_sums
        new_cpd.values = values
    return new_map


def multi_step_plan(
    failure_log: str,
    *,
    store: Optional[CausalGraphStore] = None,
    failure_node: str = "task_outcome",
    strategy: Optional[str] = None,
    evolver: Optional[StrategyEvolver] = None,
    threat_level: float = 0.0,
    cpd_map: Optional[Dict[str, Any]] = None,
    auto_build_cpds: bool = True,
    action_cost: Optional[Dict[str, float]] = None,
    horizon_override: Optional[int] = None,
    uncertainty: Optional[Dict[str, float]] = None,
    execute: bool = False,
    repair_callback: Optional[Callable[[str], float]] = None,
    rng: Optional[np.random.Generator] = None,
    audit_path: Optional[str] = None,
    capability_nonce: Optional[str] = None,
    notes: str = "",
) -> MultiStepPlanReport:
    """
    一条调用完成「感知-规划-行动-学习」长视距闭环。

    Parameters
    ----------
    failure_log : 故障上下文文本 (进入诊断事务)。
    strategy : 显式指定策略; 缺省用 evolver.select(threat_level)。
    evolver : StrategyEvolver 实例; 缺省新建 (价值从 0 起)。
    threat_level : 0..1 威胁水平 — 硬覆盖强制 quarantine。
    cpd_map : 显式 CPD 集合; 缺省 auto-build noisy-OR 二值网。
    action_cost : 每节点修复成本。
    horizon_override : 覆盖策略默认 horizon。
    uncertainty : {node: 0..1} CPD 不确定度 → observe_first 的信息价值。
    execute : 是否执行首步 (需要 repair_callback)。
    repair_callback : node → 实际 ΔP (0..1)。
    """
    store = store or get_runtime_causal_store()
    dag = store.get_graph()
    evolver = evolver or StrategyEvolver()

    # 决策审计写出口 (不做决策, 只按规范记录; 无 audit_path 则静默跳过)
    emitter = AuditEmitter(sink=JsonlSink(audit_path)) if audit_path else None

    if strategy is None:
        strategy = evolver.select(threat_level, rng=rng)
    if strategy not in STRATEGY_NAMES:
        raise KeyError(f"未知策略: {strategy!r}; 可选 {STRATEGY_NAMES}")

    params = evolver.parameters_for(strategy)

    # 1) CPD 准备 (诊断与规划共享同一套定量模型)
    if cpd_map is None and auto_build_cpds:
        try:
            cpd_map = build_binary_failure_cpd_map(dag)
        except Exception:
            cpd_map = None

    # ── 审计: diagnose (用的哪版因果图, 威胁水平) ─────────────────────
    if emitter is not None:
        emitter.diagnose(
            inputs={"graph_version": store.version, "threat_level": round(threat_level, 6)},
            context={"notes": notes, "failure_context": failure_log[:500]},
        )

    # 2) 诊断
    diagnosis = causal_fault_diagnose(
        failure_log,
        store=store,
        failure_node=failure_node,
        cpd_map=cpd_map,
        auto_build_cpds=False,
        intervention_value="ok",
    )

    # 3) 反事实规划 (策略参数动态配置)
    plan = counterfactual_rollout(
        dag,
        failure_node=failure_node,
        horizon=horizon_override or int(params["horizon"]),
        cost_lambda=float(params["cost_lambda"]),
        min_effective_delta=float(params["min_effective_delta"]),
        cpd_map=cpd_map,
        action_cost=action_cost,
        uncertainty=uncertainty,
        explore_bonus=float(params.get("explore_bonus", 0.0)),
        allow_observe=bool(params.get("allow_observe", False)),
    )

    # ── 审计: plan + decide (策略选择 + 动作效用排序) ──────────────────
    if emitter is not None:
        emitter.plan(
            inputs={"graph_version": store.version, "threat_level": round(threat_level, 6)},
            decision={
                "chosen_strategy": strategy,
                "strategy_params": {k: round(float(v), 6) for k, v in params.items()
                                     if isinstance(v, (int, float))},
                "planned_actions": [
                    {"node": a.node, "action_type": a.action_type,
                     "delta_p": round(a.delta_p, 6), "cost": round(a.cost, 6),
                     "utility_step": round(a.utility_step, 6)}
                    for a in plan.planned_actions
                ],
                "total_utility": round(plan.total_utility, 6),
            },
        )
        emitter.decide(
            inputs={"graph_version": store.version, "threat_level": round(threat_level, 6)},
            decision={
                "chosen_strategy": strategy,
                "utilities": {a.node: round(a.utility_step, 6)
                               for a in plan.planned_actions},
                "first_action": plan.planned_actions[0].node
                if plan.planned_actions else None,
            },
        )

    # 4) 执行首步
    executed = False
    execution: Optional[ExecutionResult] = None
    cpd_updated: List[str] = []
    reward = plan.total_utility  # 未执行时用规划效用做弱学习信号

    if execute and repair_callback is not None and plan.planned_actions:
        first = plan.planned_actions[0]
        actual = float(repair_callback(first.node))
        cost = first.cost
        reward = actual - float(params["cost_lambda"]) * cost
        execution = ExecutionResult(
            node=first.node,
            actual_delta_p=actual,
            cost=cost,
            reward=reward,
            p_fault_before=first.p_fault_before,
            p_fault_after=first.p_fault_after,
        )
        executed = True
        # ── 审计: execute (执行了什么 primitive, 谁授权的) ──────────
        if emitter is not None:
            emitter.execute(
                inputs={"graph_version": store.version},
                execution={
                    "primitive": f"do({first.node}=ok)",
                    "status": "ok" if actual > 0 else "no_effect",
                    "capability_nonce": capability_nonce,
                    "actual_delta_p": round(actual, 6),
                },
                context={"notes": notes},
            )
        # 5a) CPD 在线更新: 修复有效性折入节点 CPD
        if cpd_map is not None and first.p_fault_before > 0:
            effectiveness = min(1.0, max(0.0, actual / max(first.p_fault_before, 1e-9)))
            cpd_map = update_cpd_from_repair(cpd_map, first.node, effectiveness)
            cpd_updated.append(first.node)

    # 5b) 策略价值回写 (EMA)
    strategy_value_after = evolver.update(strategy, reward)

    # ── 审计: learn (学到了什么: 哪些 CPD 更新了, 策略价值几何) ────────
    if emitter is not None:
        emitter.learn(
            inputs={"graph_version": store.version},
            learning={
                "cpd_updated": cpd_updated,
                "strategy_value_after": round(strategy_value_after, 6),
                "reward": round(reward, 6),
            },
        )

    # 建议动作
    actions: List[str] = []
    for a in plan.planned_actions:
        if a.action_type == OBSERVE_ACTION:
            actions.append(
                f"[observe] 先观察 '{failure_node}' 的运行时证据 (成本 {a.cost:.3f})"
            )
        else:
            actions.append(
                f"[step {a.step}] 干预 '{a.node}' (do({a.node}=ok): ΔP={a.delta_p:.3f}, "
                f"成本 {a.cost:.3f}, 效用 {a.utility_step:+.3f})"
            )
    if not actions:
        actions.append(
            "无有效动作: 基线 P(fault) 已近零或全部 Δ 低于最小有效阈值 — 扩大因果图观测节点。"
        )
    if diagnosis.recommended_actions:
        actions.append("诊断线索: " + "; ".join(diagnosis.recommended_actions[:2]))

    return MultiStepPlanReport(
        failure_context=failure_log[:500],
        diagnosis=diagnosis,
        strategy=strategy,
        threat_level=threat_level,
        plan=plan,
        executed=executed,
        execution=execution,
        cpd_updated=cpd_updated,
        strategy_value_after=strategy_value_after,
        recommended_actions=actions,
        audit_trace_id=emitter.trace_id if emitter else None,
    )
