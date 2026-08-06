"""omodul.operator_center — O2 Operator 管线: 报价 → 分配 → 支付 → 租约 → 账本。

    bids ──► ① 组合最优化分配(匈牙利 / MILP, 确定性)
                  │
                  ├─► ② 支付规则(自家 Worker 跳过; 第三方走 VCG)
                  ├─► ③ 资源全序化 + wait-for 预检 → 租约计划
                  └─► ④ 账本: decision_id 内容寻址, 可离线重放

与 O1/O3 的接缝:
  * O1 产出的 Plan IR 资源约束 → Worker.capacity / Task.demand
  * O3 的历史遥测 → Bid.cost(拟合出来的, 不是 Worker 自报的)
  * 本层 escalations → 与 O1/O3 一样, 走同一个人工升级通道
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence

from oprim._allocate import Allocation, assign_one_to_one, assign_with_capacity, welfare
from oprim._deadlock import LeaseManager, ResourceOrder, WaitForGraph
from oprim._ledger import Ledger, Problem
from oprim._payments import PaymentResult, RULES, vcg

PIPELINE_VERSION = "o2.pipeline/0.1"


@dataclass
class OperatorEscalation:
    code: str                 # UNASSIGNED_TASK | ORDER_VIOLATION | DEADLOCK_RISK | NO_BIDS
    message: str
    subjects: List[str] = field(default_factory=list)


@dataclass
class OperatorDecision:
    decision_id: str
    allocation: Allocation
    payments: Optional[PaymentResult]
    acquisition_plan: Dict[str, List[str]]         # worker_id -> 全序资源申请序列
    escalations: List[OperatorEscalation]
    ledger: Ledger
    welfare: float = 0.0

    @property
    def ok(self) -> bool:
        return not any(e.code in ("UNASSIGNED_TASK", "DEADLOCK_RISK")
                       for e in self.escalations)


def run_operator_center(problem: Problem,
                        *,
                        mode: str = "auto",        # auto | one_to_one | capacity
                        payment_rule: Optional[str] = None,   # None=自家 Worker 不计价
                        resource_ranking: Sequence[str] = (),
                        balance_weight: float = 0.0,
                        ledger: Optional[Ledger] = None) -> OperatorDecision:
    led = ledger or Ledger()
    escalations: List[OperatorEscalation] = []

    if not problem.bids:
        escalations.append(OperatorEscalation("NO_BIDS", "没有任何可用报价", []))

    # ① 分配
    use_capacity = mode == "capacity" or (
        mode == "auto" and (any(w.max_tasks > 1 for w in problem.workers)
                            or any(t.demand for t in problem.tasks)
                            or balance_weight > 0))
    # 反事实世界必须用同一个分配器, 否则 VCG 支付算的是两个不同机制的差值
    allocator: Callable[[Problem], Allocation] = (
        (lambda pr: assign_with_capacity(pr, balance_weight=balance_weight))
        if use_capacity else assign_one_to_one)
    alloc = allocator(problem)
    w = welfare(problem, alloc)
    led.record("allocate", method=alloc.method, pairs=alloc.pairs,
               unassigned=alloc.unassigned, total_cost=alloc.total_cost, welfare=w)

    if alloc.unassigned:
        escalations.append(OperatorEscalation(
            "UNASSIGNED_TASK",
            f"{len(alloc.unassigned)} 个任务无人可接(技能不匹配或容量不足)",
            list(alloc.unassigned)))

    # ② 支付
    pay: Optional[PaymentResult] = None
    if payment_rule:
        fn = RULES[payment_rule]
        pay = fn(problem, alloc, allocator=allocator) if fn is vcg else fn(problem, alloc)
        led.record("payment", rule=pay.rule, payments=pay.payments, solves=pay.solves)

    # ③ 死锁安全的执行计划
    order = ResourceOrder(list(resource_ranking) if resource_ranking else
                          sorted({r for t in problem.tasks for r in t.resources}))
    tmap = {t.id: t for t in problem.tasks}
    plan: Dict[str, List[str]] = {}
    for wid, tids in alloc.by_worker().items():
        needed = [r for tid in tids for r in tmap[tid].resources]
        plan[wid] = order.plan(needed)
        bad = order.violates(plan[wid])
        if bad:
            escalations.append(OperatorEscalation("ORDER_VIOLATION",
                                                  f"{wid} 的申请序列存在逆序 {bad}", [wid]))
    led.record("lease_plan", plan=plan, order=list(order.rank))

    # 预检: 全序申请后 wait-for 应恒无环(作为断言留着)
    lm = LeaseManager(order=order)
    wfg = WaitForGraph()
    for wid in sorted(plan):
        for r in plan[wid]:
            holder = lm.held.get(r)
            if holder and holder.holder != wid:
                if wfg.would_deadlock(wid, holder.holder):
                    escalations.append(OperatorEscalation(
                        "DEADLOCK_RISK", f"{wid} 等待 {holder.holder} 会成环", [wid, r]))
                wfg.add_wait(wid, holder.holder, r)
            else:
                lm.acquire(wid, r, now=0.0)
    cycles = wfg.cycles()
    if cycles:
        escalations.append(OperatorEscalation("DEADLOCK_RISK",
                                              f"等待图中检测到 {len(cycles)} 个环",
                                              [">".join(c) for c in cycles]))

    for e in escalations:
        led.record("escalate", code=e.code, message=e.message, subjects=e.subjects)

    blob = "|".join([problem.digest(), alloc.method, payment_rule or "none",
                     str(balance_weight), ",".join(order.rank), PIPELINE_VERSION])
    did = "dec_" + hashlib.sha256(blob.encode()).hexdigest()[:20]
    return OperatorDecision(did, alloc, pay, plan, escalations, led, round(w, 6))


def render_decision(d: OperatorDecision) -> str:
    lines = [f"decision_id : {d.decision_id}",
             f"方法        : {d.allocation.method}   总成本={d.allocation.total_cost}   "
             f"福利={d.welfare}", "", "分配: "]
    for wid, tids in d.allocation.by_worker().items():
        lines.append(f"  {wid:<10} ← {', '.join(tids)}")
    if d.allocation.unassigned:
        lines.append(f"  (未分配)   {', '.join(d.allocation.unassigned)}")
    if d.payments:
        lines += ["", f"支付({d.payments.rule}, {d.payments.solves} 次求解): "]
        for wid, amt in sorted(d.payments.payments.items()):
            lines.append(f"  {wid:<10} {amt:>12.4f}")
        lines.append(f"  {'合计':<10} {d.payments.total():>12.4f}")
        if "_warning" in d.payments.detail:
            lines.append(f"  ⚠ {d.payments.detail['_warning']}")
    if d.acquisition_plan:
        lines += ["", "资源申请顺序(全序化, 按此顺序申请永不死锁): "]
        for wid, seq in sorted(d.acquisition_plan.items()):
            if seq:
                lines.append(f"  {wid:<10} {' → '.join(seq)}")
    if d.escalations:
        lines += ["", "升级给人: "]
        for e in d.escalations:
            lines.append(f"  [{e.code}] {e.message}  {e.subjects}")
    lines += ["", f"账本 replay_key: {d.ledger.replay_key()}"]
    return "\n".join(lines)


__all__ = ["OperatorDecision", "OperatorEscalation", "PIPELINE_VERSION",
           "render_decision", "run_operator_center"]
