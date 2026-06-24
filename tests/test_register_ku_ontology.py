"""Tests for M-ONT-1: register_ku_ontology."""
from __future__ import annotations

from pathlib import Path

import pytest

from omodul.register_ku_ontology import (
    RegisterKuOntologyConfig,
    register_ku_ontology,
)
from oprim._aii_graph_types import RegisterKuOntologyInput


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _config(**overrides) -> RegisterKuOntologyConfig:
    base = dict(
        substrate_id="sub_001",
        knowledge_type="factual",
        llm_provider="mock",
        llm_model="mock-model",
    )
    base.update(overrides)
    return RegisterKuOntologyConfig(**base)


def _ku(
    knowledge_type: str = "factual",
    grade: str = "unverified",
    *,
    stance_holder: str | None = None,
    sub_type: str | None = None,
    grounded_by: dict | None = None,
    ku_id: str = "ku001",
    concepts: list[str] | None = None,
) -> dict:
    return {
        "id": ku_id,
        "title": "Test KU",
        "content": "Test content",
        "knowledge_type": knowledge_type,
        "grade": grade,
        "sub_type": sub_type,
        "stance_holder": stance_holder,
        "grounded_by": grounded_by or {"method": "default"},
        "concepts": concepts or [],
    }


def _input(ku: dict, edges: list[dict] | None = None) -> RegisterKuOntologyInput:
    return RegisterKuOntologyInput(ku=ku, edges=edges or [])


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRegisterKuOntology:

    def test_valid_factual_ku_completes(self, tmp_path):
        result = register_ku_ontology(
            config=_config(),
            input_data=_input(_ku("factual")),
            output_dir=tmp_path,
        )
        assert result["status"] == "completed"
        assert result["ku_id"] is not None

    def test_valid_conceptual_ku(self, tmp_path):
        result = register_ku_ontology(
            config=_config(knowledge_type="conceptual"),
            input_data=_input(_ku("conceptual", sub_type="principle")),
            output_dir=tmp_path,
        )
        assert result["status"] == "completed"

    def test_valid_positional_with_holder(self, tmp_path):
        result = register_ku_ontology(
            config=_config(knowledge_type="positional"),
            input_data=_input(_ku("positional", stance_holder="Author X")),
            output_dir=tmp_path,
        )
        assert result["status"] == "completed"
        assert result["merged"] is False

    def test_valid_procedural_ku(self, tmp_path):
        result = register_ku_ontology(
            config=_config(knowledge_type="procedural"),
            input_data=_input(_ku("procedural")),
            output_dir=tmp_path,
        )
        assert result["status"] == "completed"

    def test_valid_explanatory_ku(self, tmp_path):
        result = register_ku_ontology(
            config=_config(knowledge_type="explanatory"),
            input_data=_input(_ku("explanatory")),
            output_dir=tmp_path,
        )
        assert result["status"] == "completed"

    def test_valid_metacognitive_ku(self, tmp_path):
        result = register_ku_ontology(
            config=_config(knowledge_type="metacognitive"),
            input_data=_input(_ku("metacognitive")),
            output_dir=tmp_path,
        )
        assert result["status"] == "completed"

    def test_invalid_knowledge_type_rejected(self, tmp_path):
        result = register_ku_ontology(
            config=_config(knowledge_type="bogus"),
            input_data=_input(_ku("bogus")),
            output_dir=tmp_path,
        )
        assert result["status"] == "failed"
        assert result["ku_id"] is None
        assert any("knowledge_type" in e for e in result["validation_errors"])

    def test_grade_mandate_verified_default_rejected(self, tmp_path):
        result = register_ku_ontology(
            config=_config(),
            input_data=_input(_ku("factual", grade="verified", grounded_by={"method": "default"})),
            output_dir=tmp_path,
        )
        assert result["status"] == "failed"
        assert any("verified" in e and "default" in e for e in result["validation_errors"])

    def test_grade_mandate_verified_non_default_allowed(self, tmp_path):
        result = register_ku_ontology(
            config=_config(),
            input_data=_input(_ku("factual", grade="verified", grounded_by={"method": "peer_review"})),
            output_dir=tmp_path,
        )
        assert result["status"] == "completed"

    def test_positional_without_stance_holder_rejected(self, tmp_path):
        result = register_ku_ontology(
            config=_config(knowledge_type="positional"),
            input_data=_input(_ku("positional", stance_holder=None)),
            output_dir=tmp_path,
        )
        assert result["status"] == "failed"
        assert any("stance_holder" in e for e in result["validation_errors"])

    def test_same_as_edge_triggers_merge(self, tmp_path):
        edges = [{"source": "ku001", "target": "ku002", "relation_type": "same_as"}]
        result = register_ku_ontology(
            config=_config(),
            input_data=_input(_ku("factual"), edges=edges),
            output_dir=tmp_path,
        )
        assert result["status"] == "completed"
        assert result["merged"] is True
        # same_as does not count as written edge
        assert result["edges_written"] == 0

    def test_invalid_relation_type_discarded(self, tmp_path):
        edges = [
            {"source": "ku001", "target": "ku002", "relation_type": "bogus_relation"},
            {"source": "ku001", "target": "ku003", "relation_type": "explains"},
        ]
        result = register_ku_ontology(
            config=_config(),
            input_data=_input(_ku("factual"), edges=edges),
            output_dir=tmp_path,
        )
        assert result["status"] == "completed"
        assert result["edges_written"] == 1  # only "explains" is valid

    def test_fingerprint_in_result(self, tmp_path):
        result = register_ku_ontology(
            config=_config(),
            input_data=_input(_ku("factual")),
            output_dir=tmp_path,
        )
        assert "fingerprint" in result
        assert isinstance(result["fingerprint"], str)

    def test_failure_does_not_raise(self, tmp_path):
        # Should return failed status, not raise any exception
        result = register_ku_ontology(
            config=_config(knowledge_type="invalid_type"),
            input_data=_input(_ku("invalid_type")),
            output_dir=tmp_path,
        )
        assert result["status"] == "failed"
        assert isinstance(result, dict)

    def test_validation_errors_returned_in_result(self, tmp_path):
        result = register_ku_ontology(
            config=_config(knowledge_type="bogus"),
            input_data=_input(_ku("bogus", grade="invalid_grade")),
            output_dir=tmp_path,
        )
        assert result["status"] == "failed"
        assert len(result["validation_errors"]) >= 1

    def test_on_step_callback_called(self, tmp_path):
        steps = []
        def on_step(step, state):
            steps.append((step, state))
        result = register_ku_ontology(
            config=_config(),
            input_data=_input(_ku("factual")),
            output_dir=tmp_path,
            on_step=on_step,
        )
        assert result["status"] == "completed"
        assert any("register_ku_ontology" in s[0] for s in steps)

    def test_concepts_linked_counted(self, tmp_path):
        ku = _ku("factual", concepts=["concept_a", "concept_b", "concept_c"])
        result = register_ku_ontology(
            config=_config(),
            input_data=_input(ku),
            output_dir=tmp_path,
        )
        assert result["status"] == "completed"
        assert result["concepts_linked"] == 3
