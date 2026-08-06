"""omodul.neuro_symbolic — O1 Orchestrator 管线: 意图 → 可验证 Plan IR。

四道闸门, 任何一道不过都不进下一道, 且都产出**结构化**的修复指令:

    raw JSON ─► 闸门1 校验 ─► 闸门2 回译Diff ─► 闸门3 可行性+MUS ─► 闸门4 MaxSMT ─► Plan
                    │              │                  │                  │
                    └──────────────┴────────► RepairPayload ◄───────────┘
                                               (回灌 LLM)

RepairPayload 是唯一喂回 LLM 的东西 —— 永远不含 `c_17` 这种裸 id,
只含自然语言意图 + 具体修复动作。LLM 在这里只做两件事: 产出 IR JSON,
以及按 RepairPayload 反思重试(外层循环, 本模块不持有 LLM)。

plan_id = sha256(canonical_ir ‖ z3_version ‖ seed ‖ pipeline_version):
同输入 + 同版本 + 同种子 → 同 plan_id → 同解, 三个月后可离线重跑复现。
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from oprim._backtranslate import DiffReport, Reviewer, diff_all
from oprim._ir_compile import compile_ir, domain_constraint_meta
from oprim._ir_solve import Feasibility, Solution, check_feasible, install_determinism, optimize
from oprim._mus import explain
from oprim._plan_ir import IRError, PlanIR, parse_ir, validate

PIPELINE_VERSION = "o1.pipeline/0.1"


@dataclass
class RepairPayload:
    """回灌 LLM 的确定性反馈。stage 决定 LLM 该改什么。"""
    stage: str                                  # validate | backtranslate | feasibility | solve
    summary: str
    items: List[Dict[str, Any]] = field(default_factory=list)
    instruction: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class NeuroSymbolicResult:
    ok: bool
    plan_id: str
    stage: str
    ir: Optional[PlanIR] = None
    errors: List[IRError] = field(default_factory=list)
    diffs: List[DiffReport] = field(default_factory=list)
    feasibility: Optional[Feasibility] = None
    solution: Optional[Solution] = None
    repair: Optional[RepairPayload] = None


def compute_plan_id(ir: PlanIR, z3_version: str, seed: int) -> str:
    blob = "\n".join([ir.canonical_json(), z3_version, str(seed), PIPELINE_VERSION])
    return "plan_" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:20]


def run_neuro_symbolic(raw: Any,
                       *,
                       seed: int = 0,
                       feas_timeout_ms: int = 5000,
                       opt_timeout_ms: int = 10000,
                       reviewer: Optional[Reviewer] = None,
                       deterministic_tiebreak: bool = True,
                       strict_diff: bool = True) -> NeuroSymbolicResult:
    """O1 主管线。z3 延迟导入: 前两道闸门不需要求解器。"""
    import z3  # noqa: PLC0415 - 懒加载

    install_determinism(z3, seed)
    zver = z3.get_version_string()

    # ---- 闸门 1: schema 与类型/线性校验
    ir = parse_ir(raw)
    plan_id = compute_plan_id(ir, zver, seed)
    errs = validate(ir)
    if errs:
        return NeuroSymbolicResult(False, plan_id, "validate", ir=ir, errors=errs,
                                   repair=RepairPayload(
                                       "validate",
                                       f"IR 有 {len(errs)} 处结构/类型问题, 未进入求解",
                                       [e.as_dict() for e in errs],
                                       "逐条按 hint 修正后重新输出完整 IR JSON。不要输出解释文字。"))

    # ---- 闸门 2: 回译 diff(抓翻译幻觉)
    diffs = diff_all(ir, reviewer)
    blocked = [d for d in diffs if d.blocked]
    if strict_diff and blocked:
        return NeuroSymbolicResult(False, plan_id, "backtranslate", ir=ir, diffs=diffs,
                                   repair=RepairPayload(
                                       "backtranslate",
                                       f"{len(blocked)} 条约束的回译结果与其 intent 不一致",
                                       [{"id": d.cid, "intent": d.intent, "rendered": d.rendered,
                                         "findings": [asdict(f) for f in d.findings
                                                      if f.severity == "FAIL"]}
                                        for d in blocked],
                                       "表达式与意图对不上。修正 expr 使其严格匹配 intent, "
                                       "或修正 intent 使其如实描述 expr —— 二选一, 不要两边都改。"))

    # ---- 闸门 3: 可行性 + MUS
    c = compile_ir(ir, z3)
    protected = {x.id for x in ir.constraints if x.protected}
    feas = check_feasible(c, timeout_ms=feas_timeout_ms, protected=protected)

    if feas.status == "unknown":
        return NeuroSymbolicResult(False, plan_id, "feasibility", ir=ir, diffs=diffs,
                                   feasibility=feas,
                                   repair=RepairPayload(
                                       "feasibility",
                                       f"求解器在 {feas_timeout_ms}ms 内无法判定"
                                       f"(reason: {feas.reason_unknown})",
                                       [], "这不是不可行, 是超时。请拆分问题、收紧变量定义域, "
                                           "或升级给人工。**不要**当成矛盾去改约束。"))

    if feas.status == "unsat":
        intents, origins = {}, {}
        for x in ir.constraints:
            intents[x.id], origins[x.id] = x.intent, x.origin
        for cid in c.domain_ids:
            meta = domain_constraint_meta(ir, cid)
            if meta:
                intents[cid], origins[cid] = meta.intent, meta.origin
        items = explain(feas.mus.mus, intents, origins)
        note = "" if feas.mus.verified else "(含 unknown, 核未经完全验证, 可能非最小)"
        return NeuroSymbolicResult(False, plan_id, "feasibility", ir=ir, diffs=diffs,
                                   feasibility=feas,
                                   repair=RepairPayload(
                                       "feasibility",
                                       f"约束不可满足。最小矛盾核心含 {len(items)} 条{note}",
                                       items,
                                       "以下约束**互相**矛盾, 且去掉任意一条就不再矛盾。"
                                       "请判断哪一条是误译或过度约束, 只改那一条; "
                                       "若它们都正确, 说明需求本身冲突, 请回报用户而不是硬凑。"))

    # ---- 闸门 4: MaxSMT
    sol = optimize(c, timeout_ms=opt_timeout_ms,
                   deterministic_tiebreak=deterministic_tiebreak)
    if sol.status != "sat":
        return NeuroSymbolicResult(False, plan_id, "solve", ir=ir, diffs=diffs,
                                   feasibility=feas, solution=sol,
                                   repair=RepairPayload("solve",
                                                        f"最优化阶段返回 {sol.status}",
                                                        [], "放宽 timeout 或简化目标函数"))

    return NeuroSymbolicResult(True, plan_id, "done", ir=ir, diffs=diffs,
                               feasibility=feas, solution=sol)


__all__ = ["NeuroSymbolicResult", "PIPELINE_VERSION", "RepairPayload",
           "compute_plan_id", "run_neuro_symbolic"]
