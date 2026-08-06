"""omodul.threat_model_evolve — 威胁模型闭环演化 + 隔离决策。

蜜罐触发的「敌对态」信号回流更新 Bayesian ToM 的先验与似然, 使后续意图判断
越来越锐利; 连续敌对信号后自动 quarantined(阈值可配)。

复用 Phase 2 的 oskill.BayesianBeliefUpdater(单一来源, 纯矩阵贝叶斯更新):

    P(H | E) = P(E | H) · P(H) / Σ P(E | H_i) · P(H_i)

本模块补三块:
  1. 信号似然表: 蜜罐事件类型 → P(信号 | 每个隐藏状态) 向量;
  2. severity 调制: 弱信号(severity→0)退化为无信息(似然→1), 强信号保持原值;
  3. 威胁画像持久化: 后验 → 下一次先验 + 信号计数 + 隔离状态。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Optional

from omodul._base import BaseConfig, Trail, build_result, compute_fingerprint, write_report
from oskill._bayesian_belief_update import BayesianBeliefUpdater

# 蜜罐事件类型 → [P(信号|benign), P(信号|suspicious), P(信号|hostile)]
SIGNAL_LIKELIHOODS: Dict[str, List[float]] = {
    "probe":                [0.02, 0.30, 0.80],
    "credential_stuffing":  [0.001, 0.20, 0.90],
    "payload_injection":    [0.01, 0.25, 0.85],
    "exfiltration":         [0.001, 0.10, 0.95],
    "anomalous_io":         [0.05, 0.40, 0.70],
    "honeypot_trigger":     [0.005, 0.35, 0.92],
    "benign_activity":      [0.95, 0.30, 0.05],
}
_DEFAULT_LIKELIHOOD = [0.10, 0.30, 0.60]


class ThreatModelConfig(BaseConfig):
    """威胁模型演化配置。"""

    _omodul_name: ClassVar[str] = "threat_model_evolve"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {"signals"}
    _enabled_pillars: ClassVar[set[str]] = {"report", "decision_trail", "fingerprint"}

    states: List[str] = field(default_factory=lambda: ["benign", "suspicious", "hostile"])
    quarantine_threshold: float = 0.7           # P(hostile) ≥ 此值 → 自动隔离
    hostile_state: str = "hostile"
    persistence_name: str = "threat_profile.json"


class ThreatModelInput:
    """威胁模型输入。prior 缺省 None → 均匀先验(或读上次持久化画像)。"""

    def __init__(self,
                 signals: List[dict],
                 *,
                 prior: Optional[List[float]] = None,
                 profile_path: Optional[str] = None,
                 entity: str = "default"):
        self.signals = signals                  # [{"kind": "probe", "severity": 0.8, ...}]
        self.prior = prior
        self.profile_path = profile_path        # 持久化画像路径(读旧先验/写新后验)
        self.entity = entity


def _modulated_likelihood(kind: str, severity: float) -> List[float]:
    """severity ∈ [0,1]: 0 → 无信息(似然全 1), 1 → 表内原值。线性插值。"""
    base = SIGNAL_LIKELIHOODS.get(kind, _DEFAULT_LIKELIHOOD)
    s = max(0.0, min(1.0, severity))
    return [1.0 + (lik - 1.0) * s for lik in base]


async def threat_model_evolve(
    config: ThreatModelConfig,
    input_data: ThreatModelInput,
    output_dir: Path,
    *,
    on_step=None,
) -> dict[str, Any]:
    """对一串蜜罐/遥测信号执行贝叶斯 ToM 更新, 输出威胁画像与隔离决策。"""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    trail = Trail()

    # 先验: 显式 > 持久化画像 > 均匀
    prior: Optional[List[float]] = input_data.prior
    profile_path: Optional[Path] = None
    if prior is None and input_data.profile_path:
        profile_path = Path(input_data.profile_path).expanduser()
        if profile_path.exists():
            try:
                old = json.loads(profile_path.read_text(encoding="utf-8"))
                prior = old.get("posterior")
            except (json.JSONDecodeError, OSError):
                prior = None

    updater = BayesianBeliefUpdater(config.states, prior)
    trail.record(event="init", prior=[round(x, 6) for x in updater.posterior])

    signal_trail: List[dict[str, Any]] = []
    for i, sig in enumerate(input_data.signals):
        kind = str(sig.get("kind", "probe"))
        severity = float(sig.get("severity", 1.0))
        lik = _modulated_likelihood(kind, severity)
        updater.update(lik)
        entry = {
            "index": i, "kind": kind, "severity": severity,
            "likelihood": [round(x, 4) for x in lik],
            "posterior": [round(x, 6) for x in updater.posterior],
        }
        signal_trail.append(entry)
        trail.record(event="signal", index=i, kind=kind, severity=severity)

    hostile_prob = updater.belief(config.hostile_state)
    quarantined = hostile_prob >= config.quarantine_threshold
    trail.record(event="verdict", hostile=round(hostile_prob, 6), quarantined=quarantined)

    fingerprint = compute_fingerprint({
        "entity": input_data.entity,
        "signals": [{"kind": s.get("kind"), "severity": s.get("severity")}
                    for s in input_data.signals],
    })

    # 威胁画像持久化: 后验 → 下一次的先验 (闭环)
    profile: Dict[str, Any] = {
        "entity": input_data.entity,
        "states": config.states,
        "posterior": [round(x, 6) for x in updater.posterior],
        "quarantined": quarantined,
        "hostile_prob": round(hostile_prob, 6),
        "signal_counts": _count_signals(input_data.signals),
        "updated_at": time.time(),
    }
    persist_path: Optional[Path] = None
    if profile_path is not None:
        profile_path.parent.mkdir(parents=True, exist_ok=True)
        profile_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2),
                                encoding="utf-8")
        persist_path = profile_path
    else:
        persist_path = output_dir / config.persistence_name
        persist_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2),
                                encoding="utf-8")

    report = _render_report(config, input_data, profile, signal_trail)
    report_path = write_report(report, output_dir=output_dir,
                               name=f"threat_{fingerprint[:8]}")
    trail.write(output_dir, suffix=f"_{fingerprint[:8]}")

    return build_result(
        status="quarantined" if quarantined else "monitoring",
        fingerprint=fingerprint,
        trail=trail,
        trail_path=output_dir / f"decision_trail_{trail.run_id}_{fingerprint[:8]}.json",
        report_path=report_path,
        cost_usd=0.0,
        entity=input_data.entity,
        posterior=[round(x, 6) for x in updater.posterior],
        hostile_prob=round(hostile_prob, 6),
        quarantined=quarantined,
        quarantine_threshold=config.quarantine_threshold,
        signal_trail=signal_trail,
        profile_path=str(persist_path),
    )


def _count_signals(signals: List[dict]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for s in signals:
        kind = str(s.get("kind", "unknown"))
        out[kind] = out.get(kind, 0) + 1
    return out


def _render_report(config: ThreatModelConfig, input_data: ThreatModelInput,
                   profile: dict, signal_trail: List[dict]) -> str:
    lines = [
        f"# 威胁模型演化报告 — {input_data.entity}",
        "",
        f"- 后验信念: {profile['posterior']} (states={config.states})",
        f"- P({config.hostile_state}) = {profile['hostile_prob']}  "
        f"(阈值 {config.quarantine_threshold})",
        f"- 隔离状态: **{'QUARANTINED' if profile['quarantined'] else 'MONITORING'}**",
        f"- 信号计数: {profile['signal_counts']}",
        "",
        "## 信号轨迹 (后验逐步更新)",
        "",
    ]
    for e in signal_trail:
        lines.append(f"- [{e['index']}] {e['kind']} (severity={e['severity']}) → "
                     f"后验 {e['posterior']}")
    lines += ["", "> 本报告由 omodul.threat_model_evolve 生成。后验已持久化, "
                  "将作为下一次信号评估的先验(威胁记忆闭环)。"]
    return "\n".join(lines)


__all__ = ["SIGNAL_LIKELIHOODS", "ThreatModelConfig", "ThreatModelInput",
           "threat_model_evolve"]
