"""Tests for omodul.register_entity."""

from __future__ import annotations

import json
import os

import pytest

from omodul.register_entity import (
    RegisterEntityConfig,
    compute_fingerprint_for,
    register_entity,
)

# ---------------------------------------------------------------------------
# Test Ontology
# ---------------------------------------------------------------------------

TEST_ONTOLOGY = {
    "Decision": {"required": ["title", "decision"], "semantic_fields": ["title", "decision"]},
    "Constraint": {"required": ["rule"], "semantic_fields": ["rule"]},
    "Project": {"required": ["name"], "semantic_fields": ["name"]},
}


# ---------------------------------------------------------------------------
# Mock backend
# ---------------------------------------------------------------------------


class MockBackend:
    def __init__(self):
        self._nodes = {}
        self._edges = []

    def put_node(self, node_id, payload):
        self._nodes[node_id] = payload

    def put_edge(self, src, rel, dst):
        self._edges.append((src, rel, dst))

    def get_node(self, node_id):
        return self._nodes.get(node_id)

    def list_edges(self, node_id):
        from types import SimpleNamespace

        return [SimpleNamespace(relation=r, dst_id=d) for s, r, d in self._edges if s == node_id]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_config(ontology=None, backend=None):
    return RegisterEntityConfig(
        ontology=ontology or TEST_ONTOLOGY,
        backend=backend or MockBackend(),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_valid_decision_entity_returns_completed():
    """Valid Decision entity registers successfully."""
    backend = MockBackend()
    config = make_config(backend=backend)
    input_data = {
        "entity": {
            "entity_id": "DEC-001",
            "type": "Decision",
            "title": "Use Postgres",
            "decision": "We will use Postgres as primary DB",
        }
    }
    result = register_entity(config, input_data)
    assert result["status"] == "completed"
    assert result["findings"] == "DEC-001"
    assert result["error"] is None


def test_valid_constraint_entity():
    """Valid Constraint entity registers successfully."""
    backend = MockBackend()
    config = make_config(backend=backend)
    input_data = {
        "entity": {
            "entity_id": "CON-001",
            "type": "Constraint",
            "rule": "No unregistered entity types allowed",
        }
    }
    result = register_entity(config, input_data)
    assert result["status"] == "completed"
    assert result["findings"] == "CON-001"


def test_valid_project_entity():
    """Valid Project entity registers successfully."""
    backend = MockBackend()
    config = make_config(backend=backend)
    input_data = {
        "entity": {
            "entity_id": "PRJ-001",
            "type": "Project",
            "name": "AII Platform",
        }
    }
    result = register_entity(config, input_data)
    assert result["status"] == "completed"
    assert result["findings"] == "PRJ-001"


def test_fingerprint_is_computed_and_returned():
    """Fingerprint is computed and returned in result."""
    config = make_config()
    input_data = {
        "entity": {
            "entity_id": "DEC-002",
            "type": "Decision",
            "title": "Use Redis",
            "decision": "Redis for caching",
        }
    }
    result = register_entity(config, input_data)
    assert result["fingerprint"] is not None
    assert len(result["fingerprint"]) == 64  # sha256 hex


def test_decision_trail_returned_with_steps():
    """decision_trail is returned and contains step records."""
    config = make_config()
    input_data = {
        "entity": {
            "entity_id": "DEC-003",
            "type": "Decision",
            "title": "Microservices",
            "decision": "Adopt microservices architecture",
        }
    }
    result = register_entity(config, input_data)
    trail = result["decision_trail"]
    assert trail["omodul"] == "register_entity"
    assert trail["status"] == "completed"
    step_names = [s["step"] for s in trail["steps"]]
    assert "ontology_validate" in step_names
    assert "compute_fingerprint" in step_names
    assert "put_node" in step_names


def test_invalid_entity_type_fails_gracefully():
    """Unregistered entity type returns status=failed, no raise, findings=None."""
    config = make_config()
    input_data = {
        "entity": {
            "entity_id": "X-001",
            "type": "UnknownType",
            "name": "whatever",
        }
    }
    result = register_entity(config, input_data)
    assert result["status"] == "failed"
    assert result["findings"] is None
    assert result["error"] is not None
    assert result["error"]["code"] == "ERR_ONTOLOGY_VIOLATION"


def test_duplicate_entity_same_fingerprint():
    """Two entities with same semantic content produce the same fingerprint."""
    backend = MockBackend()
    config = make_config(backend=backend)
    entity_base = {"type": "Decision", "title": "Same title", "decision": "Same decision"}

    input1 = {"entity": {"entity_id": "DEC-A", **entity_base}}
    input2 = {"entity": {"entity_id": "DEC-B", **entity_base}}

    r1 = register_entity(config, input1)
    r2 = register_entity(config, input2)

    assert r1["fingerprint"] == r2["fingerprint"]
    # Both registered (dedup is service layer's responsibility)
    assert r1["status"] == "completed"
    assert r2["status"] == "completed"


def test_missing_required_field_fails():
    """Entity missing a required field returns status=failed."""
    config = make_config()
    input_data = {
        "entity": {
            "entity_id": "DEC-999",
            "type": "Decision",
            # missing 'title' and 'decision'
        }
    }
    result = register_entity(config, input_data)
    assert result["status"] == "failed"
    assert result["findings"] is None
    assert result["error"]["code"] == "ERR_ONTOLOGY_VIOLATION"


def test_compute_fingerprint_for_function():
    """compute_fingerprint_for returns deterministic sha256 string."""
    config = make_config()
    input_data = {
        "entity": {
            "entity_id": "P-001",
            "type": "Project",
            "name": "Alpha Project",
        }
    }
    fp1 = compute_fingerprint_for(config, input_data)
    fp2 = compute_fingerprint_for(config, input_data)
    assert fp1 == fp2
    assert isinstance(fp1, str)
    assert len(fp1) == 64


def test_output_dir_creates_decision_trail_json(tmp_path):
    """When output_dir is set, decision_trail.json is written."""
    config = make_config()
    input_data = {
        "entity": {
            "entity_id": "DEC-010",
            "type": "Decision",
            "title": "Write trails",
            "decision": "Always write trails",
        }
    }
    out_dir = str(tmp_path / "trails")
    result = register_entity(config, input_data, output_dir=out_dir)
    trail_file = os.path.join(out_dir, "decision_trail.json")
    assert os.path.exists(trail_file)
    with open(trail_file) as f:
        on_disk = json.load(f)
    assert on_disk["omodul"] == "register_entity"
    assert on_disk["status"] == result["decision_trail"]["status"]


def test_on_step_callback_is_called():
    """on_step callback is invoked for each trail step."""
    config = make_config()
    input_data = {
        "entity": {
            "entity_id": "DEC-020",
            "type": "Decision",
            "title": "Callback test",
            "decision": "Callbacks work",
        }
    }
    seen_steps = []
    register_entity(config, input_data, on_step=lambda s: seen_steps.append(s["step"]))
    assert "ontology_validate" in seen_steps
    assert "compute_fingerprint" in seen_steps
    assert "put_node" in seen_steps


def test_return_dict_has_all_required_keys():
    """Result dict contains all seven required keys."""
    config = make_config()
    input_data = {
        "entity": {
            "entity_id": "DEC-030",
            "type": "Decision",
            "title": "Key check",
            "decision": "All keys present",
        }
    }
    result = register_entity(config, input_data)
    for key in (
        "findings",
        "status",
        "error",
        "fingerprint",
        "decision_trail",
        "report_path",
        "cost_usd",
    ):
        assert key in result, f"Missing key: {key}"
    assert result["report_path"] is None
    assert result["cost_usd"] == 0.0


def test_entity_stored_in_backend():
    """After successful registration, the entity is retrievable from backend."""
    backend = MockBackend()
    config = make_config(backend=backend)
    input_data = {
        "entity": {
            "entity_id": "CON-042",
            "type": "Constraint",
            "rule": "No direct DB access from frontend",
        }
    }
    result = register_entity(config, input_data)
    assert result["status"] == "completed"
    stored = backend.get_node("CON-042")
    assert stored is not None
    assert stored["rule"] == "No direct DB access from frontend"
