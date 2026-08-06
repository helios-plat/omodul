"""O1 diagnostic transaction.

Triggered when an O3 task repeatedly crashes.
Enumerates candidate nodes on the failure path, performs do-calculus
interventions (structural + quantitative when CPDs are present), and
returns a structured Root-Cause report.
No LLM guessing – only structural + interventional evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

import networkx as nx

try:
    from obase.causal_graph_store import CausalGraphStore, get_runtime_causal_store
    from oprim._do_calculus_intervention import (
        _do_calculus_intervention,
        _extract_p_fault,
        build_binary_failure_cpd_map,
    )
except ImportError:  # pragma: no cover - exercised only in minimal envs
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root))
    from obase.causal_graph_store import CausalGraphStore, get_runtime_causal_store
    from oprim._do_calculus_intervention import (
        _do_calculus_intervention,
        _extract_p_fault,
        build_binary_failure_cpd_map,
    )


@dataclass
class NodeInterventionResult:
    node_id: str
    intervention_value: Any
    effect_on_failure: str  # "eliminates_failure" | "strongly_reduces" | "reduces" | "no_effect" | "unknown"
    structural_paths: List[Dict]
    p_fault_after_do: Optional[float] = None
    delta_p_fault: Optional[float] = None  # observational - interventional (positive = improvement)
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CausalDiagnosisReport:
    failure_context: str
    candidate_nodes: List[str]
    interventions: List[NodeInterventionResult]
    root_cause_candidates: List[str]
    confidence: float
    structured_summary: Dict[str, Any]
    recommended_actions: List[str]
    quantitative: bool = False


def causal_fault_diagnose(
    failure_log: str,
    *,
    store: Optional[CausalGraphStore] = None,
    failure_node: str = "task_outcome",
    candidate_nodes: Optional[Sequence[str]] = None,
    intervention_value: Any = "ok",  # force the node into healthy state
    cpd_map: Optional[Dict[str, Any]] = None,
    auto_build_cpds: bool = True,
    observational_p_fault: Optional[float] = None,
    use_cache: bool = True,
) -> CausalDiagnosisReport:
    """
    Exhaustively test each candidate node by intervening (do-calculus).

    Modes
    -----
    - Structural (always): path existence after mutilation + path-frequency ranking.
    - Quantitative (when cpd_map or auto_build_cpds=True): compute exact
      P(failure_node = fault | do(node = ok)) and rank by the reduction
      in failure probability.

    Parameters
    ----------
    intervention_value : the healthy state we force (default "ok").
    cpd_map : optional pre-built TabularCPD dictionary.
    auto_build_cpds : if True and no cpd_map given, synthesise a noisy-OR
                      binary failure network automatically.
    observational_p_fault : if known from runtime metrics, used to compute
                            delta = observational - interventional.
    """
    store = store or get_runtime_causal_store()
    dag = store.get_graph()

    if not dag.nodes:
        return CausalDiagnosisReport(
            failure_context=failure_log,
            candidate_nodes=[],
            interventions=[],
            root_cause_candidates=[],
            confidence=0.0,
            structured_summary={"error": "empty causal graph"},
            recommended_actions=["populate CausalGraphStore with component topology"],
        )

    # Default candidates = all ancestors of the failure node
    if candidate_nodes is None:
        if failure_node in dag:
            candidate_nodes = list(nx.ancestors(dag, failure_node))
        else:
            candidate_nodes = list(dag.nodes)

    # Prepare CPDs for quantitative mode
    quantitative = False
    if cpd_map is None and auto_build_cpds:
        try:
            cpd_map = build_binary_failure_cpd_map(dag)
            quantitative = True
        except Exception:
            cpd_map = None
    elif cpd_map is not None:
        quantitative = True

    # Observational baseline (optional)
    if observational_p_fault is None and quantitative and failure_node in (cpd_map or {}):
        # Quick observational query with no intervention
        try:
            from pgmpy.models import DiscreteBayesianNetwork
            from pgmpy.inference import VariableElimination

            model = DiscreteBayesianNetwork(list(dag.edges()))
            for n in dag.nodes:
                if n not in model.nodes():
                    model.add_node(n)
            for cpd in cpd_map.values():
                model.add_cpds(cpd)
            infer = VariableElimination(model)
            q = infer.query(variables=[failure_node], show_progress=False)
            states = list(q.state_names[failure_node]) if failure_node in q.state_names else list(range(q.cardinality[0]))
            probs = q.values.ravel()
            dist = {str(states[i]): float(probs[i]) for i in range(len(probs))}
            observational_p_fault = _extract_p_fault(dist)
        except Exception:
            observational_p_fault = None

    interventions: List[NodeInterventionResult] = []
    scored: List[tuple] = []  # (score, node_id) for ranking

    for node in candidate_nodes:
        if node == failure_node:
            continue

        raw = _do_calculus_intervention(
            dag,
            target_node=node,
            intervention_value=intervention_value,
            outcome_nodes=[failure_node] if failure_node in dag else None,
            cpd_map=cpd_map,
            use_cache=use_cache,
        )

        paths = raw.get("structural_effect_paths", [])
        p_after = None
        delta = None
        effect = "unknown"

        if quantitative and raw.get("post_intervention_distribution"):
            dist = raw["post_intervention_distribution"].get(failure_node)
            p_after = _extract_p_fault(dist)
            if p_after is not None and observational_p_fault is not None:
                delta = observational_p_fault - p_after
                if delta >= 0.4:
                    effect = "eliminates_failure"
                elif delta >= 0.15:
                    effect = "strongly_reduces"
                elif delta > 0.02:
                    effect = "reduces"
                else:
                    effect = "no_effect"
            elif p_after is not None:
                # No observational baseline – use absolute post-do probability
                if p_after < 0.1:
                    effect = "strongly_reduces"
                elif p_after < 0.3:
                    effect = "reduces"
                else:
                    effect = "on_causal_path"
            score = delta if delta is not None else (1.0 - (p_after or 0.5))
        else:
            # Pure structural
            if paths:
                effect = "on_causal_path"
                score = 1.0
            else:
                effect = "no_structural_path"
                score = 0.0

        interventions.append(
            NodeInterventionResult(
                node_id=node,
                intervention_value=intervention_value,
                effect_on_failure=effect,
                structural_paths=paths,
                p_fault_after_do=p_after,
                delta_p_fault=delta,
                raw=raw,
            )
        )
        scored.append((score, node))

    # Rank: highest reduction first; fall back to structural path frequency
    # O(V+E) 拓扑 DP 计路径频次 (替代 all_simple_paths 指数级枚举)
    from oprim._inference_cache import path_frequency_counts  # noqa: PLC0415

    path_counts: Dict[str, int] = path_frequency_counts(dag, candidate_nodes, failure_node)

    if quantitative:
        ranked = [n for _, n in sorted(scored, key=lambda t: t[0], reverse=True) if _ > 0.0]
    else:
        ranked = sorted(
            [n for _, n in scored if _ > 0],
            key=lambda n: path_counts.get(n, 0),
            reverse=True,
        )

    confidence = min(0.95, 0.45 + 0.12 * len(ranked)) if ranked else 0.1
    if quantitative and any(i.delta_p_fault and i.delta_p_fault > 0.2 for i in interventions):
        confidence = min(0.98, confidence + 0.15)

    summary = {
        "failure_node": failure_node,
        "num_candidates_tested": len(interventions),
        "root_cause_ranked": ranked[:5],
        "path_frequency": {k: v for k, v in path_counts.items() if v > 0},
        "method": (
            "do-calculus + quantitative CPD (noisy-OR binary network)"
            if quantitative
            else "do-calculus structural intervention + path enumeration"
        ),
        "observational_p_fault": observational_p_fault,
        "quantitative": quantitative,
    }

    actions = []
    if ranked:
        top = ranked[0]
        top_res = next(i for i in interventions if i.node_id == top)
        if top_res.delta_p_fault is not None:
            actions.append(
                f"Intervene on '{top}' (force healthy): expected ΔP(fault) ≈ "
                f"{top_res.delta_p_fault:.3f} (from {observational_p_fault:.3f} → "
                f"{top_res.p_fault_after_do:.3f}). Circuit-break / retry."
            )
        else:
            actions.append(
                f"Isolate / circuit-break component '{top}' and retry the task."
            )
        actions.append(
            f"Inspect runtime metrics / logs of '{top}' for the observed failure window."
        )
        if len(ranked) > 1:
            actions.append(f"Secondary suspects: {', '.join(ranked[1:3])}.")
    else:
        actions.append(
            "No structural/quantitative root cause found; expand the causal graph "
            "with more observability nodes (timeouts, rate-limits, external APIs)."
        )

    return CausalDiagnosisReport(
        failure_context=failure_log[:500],
        candidate_nodes=list(candidate_nodes),
        interventions=interventions,
        root_cause_candidates=ranked,
        confidence=confidence,
        structured_summary=summary,
        recommended_actions=actions,
        quantitative=quantitative,
    )
