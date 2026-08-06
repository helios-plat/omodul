"""omodul.closed_loop_intervene — O1 闭环事务: 感知-决策-行动-学习。

一条调用完成「诊断 → 效用排序 → 执行/模拟干预 → 收集观测 → 在线更新因果模型」:

    Phase2 诊断 ──► oprim.select_intervention (期望效用) ──► 执行/模拟
         ▲                                                    │
         │                                                    ▼
    CPD 更新 ◄─── 观测 (父配置 + 成败) ◄─── 真实反馈 ◄─────────┘

干预选择公式:
    a* = argmax_a ( ΔP(success|do(a)) − λ·C(a) − ρ·risk(a) )
ΔP 来自 Phase 2 的定量 do-calculus 结果(缺省用 CPD 级 do: P(success|parent=x) 对比基线)。

两条闭环线:
  1. 因果参数线: 观测回灌 oskill.update_cpd (Dirichlet/EMA) → 模型变准;
  2. 策略演化线: 更新后的 CPD 改变下一轮 ΔP 估计 → 干预策略随之演化。
"""

from __future__ import annotations

import inspect
import json
import random
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, ClassVar, List, Optional

from omodul._base import BaseConfig, Trail, build_result, compute_fingerprint, write_report
from oprim._audit_emit import AuditEmitter, JsonlSink
from oprim._expected_utility_select import (InterventionCandidate,
                                            from_diagnosis_report,
                                            select_intervention)
from oskill._online_cpd_update import CategoricalCPD, config_key, update_cpd


class ClosedLoopConfig(BaseConfig):
    """闭环事务配置。"""

    _omodul_name: ClassVar[str] = "closed_loop_intervene"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {"cpd", "interventions"}
    _enabled_pillars: ClassVar[set[str]] = {"report", "decision_trail", "fingerprint"}

    lambda_cost: float = 1.0                    # 成本权重 λ
    risk_aversion: float = 1.0                  # 风险厌恶 ρ
    drop_negative: bool = True                  # 丢弃非正效用动作(不输出 least-bad)
    update_mode: str = "dirichlet"              # dirichlet | ema
    dirichlet_strength: float = 1.0
    ema_alpha: float = 0.1
    simulate: bool = True                       # True: 用当前 CPD 蒙特卡洛模拟干预结果
    rounds: int = 1                             # 闭环轮数(每轮: 选择→执行→更新)
    seed: int = 0
    parent_name: str = "mode"                   # 干预的父变量名(元数据)
    baseline_config: str = "degraded"           # 基线(不干预)父配置
    fault_state: str = "fault"
    execute_fn: Optional[Callable] = None       # 注入执行器: (action_id, target_value) -> {"success": bool}
    audit_path: Optional[str] = None            # 审计 JSONL 路径; None = 不写审计


class ClosedLoopInput:
    """闭环事务输入(简单容器, 非 Pydantic —— 允许直接传 dataclass 值)。"""

    def __init__(self,
                 cpd: CategoricalCPD,
                 *,
                 diagnosis: Optional[dict] = None,
                 interventions: Optional[List[dict]] = None,
                 description: str = "",
                 threat_level: float = 0.0,
                 capability_nonce: Optional[str] = None,
                 graph_version: Optional[int] = None,
                 notes: str = ""):
        self.cpd = cpd
        self.diagnosis = diagnosis
        self.interventions = interventions
        self.description = description
        self.threat_level = threat_level          # 0..1, 审计 inputs
        self.capability_nonce = capability_nonce  # 谁授权的, 审计 execution
        self.graph_version = graph_version        # 用的哪版因果图, 审计 inputs
        self.notes = notes


def _build_candidates(input_data: ClosedLoopInput, cfg: ClosedLoopConfig) -> List[InterventionCandidate]:
    """候选来源优先级: 显式 interventions > Phase2 诊断报告 > CPD 自动推导。"""
    if input_data.interventions:
        try:
            base_fault = input_data.cpd.p_fault(cfg.baseline_config, cfg.fault_state)
        except KeyError:
            base_fault = 0.5
        out = []
        for it in input_data.interventions:
            dp = float(it.get("delta_p", 0.0))
            if "delta_p" not in it:
                # 未给 ΔP → 从 CPD 现算 (do-calculus-lite)
                target = str(it.get("target_value", ""))
                try:
                    dp = base_fault - input_data.cpd.p_fault(config_key(target), cfg.fault_state)
                except KeyError:
                    dp = 0.0
            out.append(InterventionCandidate(
                action_id=str(it.get("action_id") or it.get("id")),
                delta_p=dp,
                cost=float(it.get("cost", 0.0)),
                risk=float(it.get("risk", 0.0)),
                description=str(it.get("description", "")),
            ))
        return [c for c in out if c.action_id]

    if input_data.diagnosis:
        cands = from_diagnosis_report(input_data.diagnosis)
        if cands:
            return cands

    # CPD 自动推导: 每个非基线父配置 → do(parent=v) 候选
    baseline = cfg.baseline_config
    base_fault = input_data.cpd.p_fault(baseline, cfg.fault_state)
    out = []
    for cfg_key in sorted(input_data.cpd.counts):
        if cfg_key == baseline:
            continue
        v = cfg_key.split("|")[0]
        try:
            dp = base_fault - input_data.cpd.p_fault(cfg_key, cfg.fault_state)
        except KeyError:
            continue
        out.append(InterventionCandidate(
            action_id=f"do_{cfg.parent_name}={v}", delta_p=dp,
            cost=0.0, risk=0.0,
            description=f"干预: 将 {cfg.parent_name} 设为 {v} (CPD 自动推导)",
        ))
    return out


async def closed_loop_intervene(
    config: ClosedLoopConfig,
    input_data: ClosedLoopInput,
    output_dir: Path,
    *,
    on_step=None,
) -> dict[str, Any]:
    """闭环事务主入口。

    返回 dict: {status: executed|no_action, fingerprint, selection, executed,
                cpd_before, cpd_after, p_fault_before/after, rounds, report_path}
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    trail = Trail()
    rng = random.Random(config.seed)

    # 决策审计写出口 (不做决策, 只按规范记录; 无 audit_path 则静默跳过)
    emitter = AuditEmitter(sink=JsonlSink(config.audit_path)) if config.audit_path else None

    cpd = input_data.cpd
    if on_step:
        on_step({"type": "closed_loop", "stage": "begin", "rounds": config.rounds})

    fingerprint = compute_fingerprint({
        "cpd": cpd.to_dict(),
        "interventions": input_data.interventions or [],
        "diagnosis": input_data.diagnosis or {},
    })

    # ── 审计: diagnose (用的是哪版图/CPD, 威胁水平多少) ──────────────
    if emitter is not None:
        candidates0 = _build_candidates(input_data, config)
        emitter.diagnose(
            inputs={
                "graph_version": input_data.graph_version,
                "cpd_version": cpd.version,
                "threat_level": round(input_data.threat_level, 6),
                "candidate_count": len(candidates0),
            },
            context={"notes": input_data.notes, "description": input_data.description},
        )

    executed: List[dict[str, Any]] = []
    for rnd in range(max(1, config.rounds)):
        candidates = _build_candidates(input_data, config)
        sel = select_intervention(candidates,
                                  lambda_cost=config.lambda_cost,
                                  risk_aversion=config.risk_aversion,
                                  drop_negative=config.drop_negative)
        trail.record(event="select", round=rnd, best=sel.best.action_id if sel.best else None,
                     ranked=len(sel.ranked), rejected=len(sel.rejected))

        # ── 审计: decide (为什么选这个动作 + 全部效用排序) ──────────
        if emitter is not None:
            emitter.decide(
                inputs={"cpd_version": cpd.version, "round": rnd,
                        "threat_level": round(input_data.threat_level, 6)},
                decision={
                    "chosen_strategy": sel.best.action_id if sel.best else None,
                    "utilities": {c.action_id: round(u, 6) for c, u in sel.ranked},
                    "rejected": [c.action_id for c, _ in sel.rejected],
                },
            )

        if sel.best is None:
            trail.record(event="no_action", round=rnd,
                         reason="无正效用干预(不输出 least-bad)")
            if on_step:
                on_step({"type": "closed_loop", "stage": "no_action", "round": rnd})
            break

        best = sel.best
        target_value = best.action_id.split("=")[-1] if "=" in best.action_id else best.action_id
        parent_config = config_key(target_value)

        # ── 执行 / 模拟 ─────────────────────────────────────────────
        if config.execute_fn is not None:
            fn = config.execute_fn
            if inspect.iscoroutinefunction(fn):
                outcome = await fn(best.action_id, target_value)
            else:
                outcome = fn(best.action_id, target_value)
            success = bool(outcome.get("success", False))
        elif config.simulate:
            # 蒙特卡洛: 用当前 CPD 模型 P(success|do(x)) 采样
            try:
                p_success = 1.0 - cpd.p_fault(parent_config, config.fault_state)
            except KeyError:
                p_success = 0.5
            success = rng.random() < p_success
        else:
            success = False

        observed_state = "success" if success else config.fault_state
        # 因果观测协议: 记录**实现态** parent_config, 而非意图态 ——
        # 干预未生效时(apply 失败)观测属于实际停留的配置, 记错会污染 CPD
        realized_config = str(outcome.get("parent_config") or parent_config) \
            if config.execute_fn is not None else parent_config
        cpd_version_before = cpd.version

        # ── 在线 CPD 更新 ───────────────────────────────────────────
        if config.update_mode == "dirichlet":
            cpd = update_cpd(cpd, realized_config, observed_state, mode="dirichlet",
                             strength=config.dirichlet_strength)
        else:
            cpd = update_cpd(cpd, realized_config, observed_state, mode="ema",
                             alpha=config.ema_alpha)

        executed.append({
            "round": rnd, "action_id": best.action_id,
            "target_config": parent_config, "realized_config": realized_config,
            "delta_p": best.delta_p,
            "utility": best.delta_p - config.lambda_cost * best.cost
                       - config.risk_aversion * best.risk,
            "outcome": "success" if success else "failure",
            "observed_state": observed_state,
        })
        trail.record(event="execute", round=rnd, action=best.action_id, success=success)

        # ── 审计: execute + learn (执行了什么 primitive, 谁授权的, 学到了什么) ──
        if emitter is not None:
            emitter.execute(
                inputs={"cpd_version": cpd_version_before, "round": rnd},
                execution={
                    "primitive": best.action_id,
                    "status": "ok" if success else "failed",
                    "capability_nonce": input_data.capability_nonce,
                    "realized_config": realized_config,
                },
                context={"notes": input_data.notes},
            )
            emitter.learn(
                inputs={"cpd_version": cpd_version_before, "round": rnd},
                learning={
                    "cpd_version_after": cpd_version_before + 1,
                    "updated_config": realized_config,
                    "observed_state": observed_state,
                },
            )
        if on_step:
            on_step({"type": "closed_loop", "stage": "executed", "round": rnd,
                     "action": best.action_id, "success": success})

    # ── 汇总 ────────────────────────────────────────────────────────
    p_before = {
        cfg_key: round(input_data.cpd.p_fault(cfg_key, config.fault_state), 6)
        for cfg_key in sorted(input_data.cpd.counts)
    }
    p_after = {
        cfg_key: round(cpd.p_fault(cfg_key, config.fault_state), 6)
        for cfg_key in sorted(cpd.counts)
    }
    success_count = sum(1 for e in executed if e["outcome"] == "success")
    failure_count = len(executed) - success_count

    # 持久化更新后的 CPD (跨进程在线积累)
    cpd_path = output_dir / "cpd_after.json"
    cpd_path.write_text(json.dumps(cpd.to_dict(), ensure_ascii=False, indent=2),
                        encoding="utf-8")

    audit_trace_id = emitter.trace_id if emitter else None

    report = _render_report(config, input_data, executed, p_before, p_after,
                            fingerprint, cpd_path)
    report_path = write_report(report, output_dir=output_dir,
                               name=f"closed_loop_{fingerprint[:8]}")
    trail.write(output_dir, suffix=f"_{fingerprint[:8]}")

    status = "executed" if executed else "no_action"
    return build_result(
        status=status,
        fingerprint=fingerprint,
        trail=trail,
        trail_path=output_dir / f"decision_trail_{trail.run_id}_{fingerprint[:8]}.json",
        report_path=report_path,
        cost_usd=0.0,
        rounds=len(executed),
        executed=executed,
        success_count=success_count,
        failure_count=failure_count,
        executed_failure_rate=round(failure_count / len(executed), 4) if executed else None,
        p_fault_before=p_before,
        p_fault_after=p_after,
        cpd_before=input_data.cpd.to_dict(),
        cpd_after=cpd.to_dict(),
        cpd_path=str(cpd_path),
        audit_trace_id=audit_trace_id,
    )


def _render_report(config: ClosedLoopConfig, input_data: ClosedLoopInput,
                   executed: List[dict], p_before: dict, p_after: dict,
                   fingerprint: str, cpd_path: Path) -> str:
    lines = [
        f"# 闭环干预事务报告 — {fingerprint}",
        "",
        f"- 更新模式: {config.update_mode} (λ={config.lambda_cost}, ρ={config.risk_aversion})",
        f"- 执行轮数: {len(executed)} / 配置 {config.rounds}",
        "",
        "## 干预执行记录",
        "",
    ]
    for e in executed:
        lines.append(f"- R{e['round']} `{e['action_id']}` → {e['target_config']}: "
                     f"**{e['outcome']}** (ΔP={e['delta_p']:.4f}, U={e['utility']:.4f})")
    if not executed:
        lines.append("- 无正效用干预, 未执行任何动作 (不输出 least-bad)。")
    lines += ["", "## P(fault) 演化 (干预前 → 干预后)", "",
              "| 父配置 | 干预前 | 干预后 |", "|---|---|---|"]
    for cfg_key in sorted(set(p_before) | set(p_after)):
        lines.append(f"| {cfg_key} | {p_before.get(cfg_key, '—')} | {p_after.get(cfg_key, '—')} |")
    lines += ["", f"- 更新后 CPD 已持久化: `{cpd_path}`"]
    lines += ["", "> 本报告由 omodul.closed_loop_intervene 生成。观测回灌后, "
                  "下一轮 ΔP 估计将自动使用更新后的因果模型。"]
    return "\n".join(lines)


__all__ = ["ClosedLoopConfig", "ClosedLoopInput", "closed_loop_intervene"]
