"""omodul.cholesky_scm — 连续 Cholesky SCM: 多维连续节点上的 L3 反事实。

把 Cholesky 流嵌进每个连续节点的机制 f(PA, U) = μ(PA) + L(PA)·U,
支撑多维遥测 (如 [latency_p50, latency_p99, error_rate]) 的联合 L3 溯因:

    Abduction  u_X = L⁻¹(x − μ(PA))         — 观测节点反演噪声
    Action     do(X = v)                     — 确定性干预 (割裂父依赖)
    Prediction x_Y = μ(PA) + L(PA)·u         — 夹持溯因 U 拓扑传播

与二进制故障 SCM (oprim._structural_counterfactual) 互补:
二进制管故障态 OR 网络; 本模块管连续指标向量, 残差相关由 L 保持。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import networkx as nx
import numpy as np
from oprim._cholesky_flow import CholeskyMechanism


@dataclass
class ContinuousNode:
    """连续节点: 机制 + 父节点列表。"""

    name: str
    mechanism: CholeskyMechanism
    parents: list[str] = field(default_factory=list)


class ContinuousCholeskySCM:
    """连续 Cholesky SCM (DAG 序传播)。"""

    def __init__(self, nodes: dict[str, ContinuousNode]):
        self.nodes = nodes
        dag = nx.DiGraph()
        for name, nd in nodes.items():
            dag.add_node(name)
            for p in nd.parents:
                dag.add_edge(p, name)
        if not nx.is_directed_acyclic_graph(dag):
            raise ValueError("连续 SCM 必须是 DAG")
        self.order = list(nx.topological_sort(dag))

    # ── 构造 ────────────────────────────────────────────────────────
    @classmethod
    def fit_from_data(cls, dag: Any, data: dict[str, np.ndarray], *,
                      fit: str = "linear", **fit_kw: Any) -> ContinuousCholeskySCM:
        """按 DAG 逐节点拟合条件机制。

        data[node] = (n, d_node) 观测矩阵; 父节点观测取同一行 (需对齐行序)。
        fit: "linear" (闭式条件高斯) | "mlp" (单隐层非线性)。
        """
        nodes: dict[str, ContinuousNode] = {}
        for name in nx.topological_sort(dag):
            parents = list(dag.predecessors(name))
            x = np.atleast_2d(np.asarray(data[name], dtype=float))
            if parents:
                pa = np.hstack([np.atleast_2d(np.asarray(data[p], dtype=float))
                                for p in parents])
            else:
                pa = np.zeros((x.shape[0], 0))
            mech = (CholeskyMechanism.fit_mlp(pa, x, **fit_kw) if fit == "mlp"
                    else CholeskyMechanism.fit_linear(pa, x, **fit_kw))
            nodes[name] = ContinuousNode(name=name, mechanism=mech, parents=parents)
        return cls(nodes)

    # ── 1. Abduction: u_X = L⁻¹(x − μ(PA)) ──────────────────────────
    def abduct(self, evidence: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
        """对观测节点反演噪声; 未观测节点 u=0 (N(0,I) 的 MAP) 并传播期望。"""
        values: dict[str, np.ndarray] = {}
        u_map: dict[str, np.ndarray] = {}
        for name in self.order:
            nd = self.nodes[name]
            pa_vals = (np.concatenate([values[p] for p in nd.parents])
                       if nd.parents else np.zeros(0))
            if name in evidence:
                x = np.asarray(evidence[name], dtype=float)
                values[name] = x
                u_map[name] = nd.mechanism.invert(pa_vals, x)
            else:
                values[name] = nd.mechanism.mean(pa_vals)
                u_map[name] = np.zeros(nd.mechanism.d)
        return u_map

    # ── 2+3. Action + Prediction ────────────────────────────────────
    def predict(self, failure_node: str, *,
                intervened: dict[str, Sequence[float]] | None = None,
                u_map: dict[str, np.ndarray] | None = None) -> np.ndarray:
        """夹持溯因 U 拓扑传播: 干预节点取固定值, 其余 x = μ(PA) + L(PA)·u。"""
        intervened = intervened or {}
        u_map = u_map or {n: np.zeros(self.nodes[n].mechanism.d)
                          for n in self.order}
        values: dict[str, np.ndarray] = {}
        for name in self.order:
            nd = self.nodes[name]
            if name in intervened:
                values[name] = np.asarray(intervened[name], dtype=float)
                continue
            pa_vals = (np.concatenate([values[p] for p in nd.parents])
                       if nd.parents else np.zeros(0))
            u = u_map.get(name, np.zeros(nd.mechanism.d))
            values[name] = nd.mechanism.mean(pa_vals) \
                + nd.mechanism.chol(pa_vals) @ u
        return np.asarray(values[failure_node], dtype=float)

    def l3_counterfactual(self, evidence: dict[str, np.ndarray],
                          intervened: dict[str, Sequence[float]],
                          failure_node: str) -> np.ndarray:
        """L3: 锚定本次观测噪声, 问"若当时 do(X=v), failure 会是什么"。"""
        u_map = self.abduct(evidence)
        return self.predict(failure_node, intervened=intervened, u_map=u_map)

    # ── L2 对照: 不锚定本次 U (U=0 期望传播) ─────────────────────────
    def l2_expected(self, failure_node: str,
                    intervened: dict[str, Sequence[float]] | None = None) -> np.ndarray:
        """E[X_Y | do(intervened)] — 平均情形 (U 取均值 0)。"""
        return self.predict(failure_node, intervened=intervened or {}, u_map={})


__all__ = ["ContinuousCholeskySCM", "ContinuousNode"]
