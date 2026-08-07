"""omodul.counterfactual_diagnose — L3 反事实诊断事务: 针对**这一次**故障。

与 L2 规划层的关系:
    do-calculus 诊断 / multi_step_plan   L2: 对"下一次强制做什么"的指导
    counterfactual_diagnose              L3: 对"这一次若当时强制健康会怎样"的答案

三步法 (委托 oprim._structural_counterfactual):
    1. Abduction  P(U | e)  均值场/边际 MAP, 锚定本次观测噪声
    2. Action     do(X=ok)  割裂入边 + 确定性机制
    3. Prediction 夹持溯因 U 传播 → P(task_outcome | do(X=ok), U~P(U|e))

每个候选节点产出三层对照 (可审计的 L3 记录):
    factual_p_fault    P(Y | e)            — 本次事实 (观测值)
    l2_p_fault_after_do  P(Y | do(X=ok))   — 不锚定本次 U (平均情形)
    l3_p_fault_counterfactual              — 锚定本次噪声 (若当时…)
    l3_delta           factual − l3        — 本次若能压住的故障质量
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from oprim._structural_counterfactual import StructuralSCM


@dataclass
class CounterfactualReport:
    """单个候选节点的 L3 反事实记录 (可审计)。"""

    node: str
    factual_p_fault: float  # P(Y | e) — 本次事实
    l2_p_fault_after_do: float  # P(Y | do(X=ok)) — L2 平均情形
    l3_p_fault_counterfactual: float  # P(Y | do(X=ok), U~P(U|e)) — L3 本次
    l2_delta: float  # factual − l2 (平均可压降)
    l3_delta: float  # factual − l3 (本次可压降)
    ranking_score: float  # 排序分 = l3_delta (本次口径)

    def as_dict(self) -> dict[str, Any]:
        return {
            k: (round(float(v), 6) if isinstance(v, (int, float)) else v)
            for k, v in self.__dict__.items()
        }


@dataclass
class CounterfactualDiagnosisReport:
    """L3 反事实诊断报告。"""

    failure_node: str
    evidence: dict[str, str]
    ranking: list[str]  # 按 ranking_score 降序 (本次真凶优先)
    reports: list[CounterfactualReport]  # 与 ranking 同序
    abduction: dict[str, float]  # P(U_X=1 | e) 均值场后验
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        if not self.reports:
            return "无候选节点"
        top = self.reports[0]
        return (
            f"本次故障最可能被 '{top.node}' 压制: L3 压降 {top.l3_delta:.3f} "
            f"(L2 口径 {top.l2_delta:.3f})"
        )


def counterfactual_diagnose(
    store: Any,
    *,
    failure_node: str = "task_outcome",
    factual_evidence: dict[str, str],
    candidate_nodes: Sequence[str] | None = None,
    cpd_map: dict[str, Any] | None = None,
    auto_build_cpds: bool = True,
    sweeps: int = 5,
) -> CounterfactualDiagnosisReport:
    """对**这一次**故障做 L3 反事实诊断。

    Args:
        store: CausalGraphStore (或任何有 get_graph() 的对象)。
        failure_node: 结果节点 Y。
        factual_evidence: 本次观测 {"节点": "ok"|"fault"} — 必须包含 failure_node。
        candidate_nodes: 候选干预节点; 缺省 = 能影响 failure_node 的全部祖先。
        cpd_map: 显式 CPD 集合; 缺省 auto-build noisy-OR 二值网。
        sweeps: 均值场溯因迭代轮数。

    Returns:
        CounterfactualDiagnosisReport — ranking 按 l3_delta 降序,
        reports 含 factual / L2 / L3 三层对照 (可审计)。
    """
    dag = store.get_graph()
    if failure_node not in dag:
        raise KeyError(f"failure_node '{failure_node}' 不在因果图中")

    # CPD 准备 (noisy-OR 二值网)
    if cpd_map is None and auto_build_cpds:
        from oprim._do_calculus_intervention import build_binary_failure_cpd_map

        try:
            cpd_map = build_binary_failure_cpd_map(dag)
        except Exception:
            cpd_map = None

    # 候选节点: 能影响 failure_node 的祖先 (排除 failure 自身)
    if candidate_nodes is None:
        candidate_nodes = [
            n for n in dag.nodes if n != failure_node and nx_has_path(dag, n, failure_node)
        ]
    candidates = [n for n in candidate_nodes if n != failure_node]

    # 1. 显式噪声 SCM + Abduction (均值场/边际 MAP)
    scm = StructuralSCM.from_graph(dag, cpd_map)
    u_posterior = scm.abduct(factual_evidence, sweeps=sweeps)

    factual_p = (
        1.0
        if str(factual_evidence.get(failure_node, "")).lower() in ("fault", "1", "true", "fail")
        else 0.0
    )

    notes: list[str] = []
    if cpd_map is None:
        notes.append("无 CPD → 使用节点属性 p_fail/cond_fail 拟合噪声参数")

    # 2+3. 逐候选: Action(do=ok) + Prediction (L2 与 L3 对照)
    reports: list[CounterfactualReport] = []
    for node in candidates:
        l2 = scm.l2_p_fault([node], failure_node)
        # 精确 twin-network 反事实 (锚定 factual 证据的同一份外生噪声)
        l3 = scm.l3_p_fault([node], failure_node, u_posterior, evidence=factual_evidence)
        l2_delta = factual_p - l2
        l3_delta = factual_p - l3
        reports.append(
            CounterfactualReport(
                node=node,
                factual_p_fault=factual_p,
                l2_p_fault_after_do=l2,
                l3_p_fault_counterfactual=l3,
                l2_delta=l2_delta,
                l3_delta=l3_delta,
                ranking_score=l3_delta,
            )
        )

    # 确定性排序: l3_delta 降序 → l2_delta 降序 → 节点字典序
    reports.sort(key=lambda r: (-r.ranking_score, -r.l2_delta, r.node))
    return CounterfactualDiagnosisReport(
        failure_node=failure_node,
        evidence=dict(factual_evidence),
        ranking=[r.node for r in reports],
        reports=reports,
        abduction={k: round(v, 6) for k, v in u_posterior.items()},
        notes=notes,
    )


def nx_has_path(dag: Any, source: str, target: str) -> bool:
    import networkx as nx  # noqa: PLC0415

    try:
        return nx.has_path(dag, source, target)
    except nx.NetworkXError:
        return False


__all__ = ["CounterfactualDiagnosisReport", "CounterfactualReport", "counterfactual_diagnose"]
