"""omodul.observer — O3 Observer 编排: 模拟 → 提交。

破坏性动作落地之前, 先在沙箱里真跑一遍。不是 prompt 里写
"Let's think step by step", 是 checkout 快照 → 应用候选 → 真实执行 → 稠密打分。

本模块是编排层(机制/业务分离): 快照库、沙箱池、探针链全部由调用方注入,
管线只负责 闸门 → 并行 rollout → 确定性排名 → 阈值/稳定性裁决 → Verdict。
原子机制在 oprim._actions / _snapshot / _sandbox / _reward / _lookahead。

三条铁律:
1. 不可逆动作永不进搜索(可逆性闸门在 rollout 之前生效)。
2. 不输出 least-bad: 低于 min_reward 一律升级给人。
3. Verdict 强制携带 divergences(沙箱与生产的已知差异, 调用方声明)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from oprim._actions import ActionPlan, Applier, gate
from oprim._lookahead import Divergence, Rollout, Verdict, lookahead, render_verdict
from oprim._reward import Probe
from oprim._sandbox import SandboxPool
from oprim._snapshot import SnapshotStore


@dataclass
class ObserverConfig:
    min_reward: float = 0.999
    stability_check: bool = False
    max_parallel: int = 4
    seed: int = 0


def run_observer_lookahead(plans: Sequence[ActionPlan],
                           base_dir: str,
                           store: SnapshotStore,
                           pool: SandboxPool,
                           probes: Sequence[Probe],
                           *,
                           applier: Optional[Applier] = None,
                           config: Optional[ObserverConfig] = None,
                           divergences: Sequence[Divergence] = ()) -> Verdict:
    """跑一轮单步 lookahead(编排层门面)。

    返回值直接给上层: verdict.chosen 可执行 / verdict.escalations 走人审。
    """
    cfg = config or ObserverConfig()
    return lookahead(
        plans, base_dir, store, pool, probes,
        applier=applier,
        min_reward=cfg.min_reward,
        stability_check=cfg.stability_check,
        max_parallel=cfg.max_parallel,
        divergences=divergences,
        seed=cfg.seed,
    )


__all__ = ["ObserverConfig", "run_observer_lookahead"]
