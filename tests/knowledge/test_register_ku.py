"""Tests for omodul.register_ku."""

from __future__ import annotations

import json
import os
import tempfile

import pytest

from omodul.register_ku import RegisterKuConfig, compute_fingerprint_for, register_ku


# ---------------------------------------------------------------------------
# Mock backend
# ---------------------------------------------------------------------------


class MockBackend:
    def __init__(self):
        self._nodes = {}

    def put_node(self, node_id, payload):
        self._nodes[node_id] = payload

    def get_node(self, node_id):
        return self._nodes.get(node_id)


def _make_ku(**overrides):
    ku = {
        "ku_id": "KU-test-001",
        "knowledge_type": "proposition",
        "natural_text": "Machine learning is a subset of artificial intelligence.",
        "symbolic_form": None,
        "vector": None,
        "vector_frozen": False,
        "epistemic_status": {
            "grade": "unverified",
            "source": None,
            "defeaters": [],
            "verified": False,
        },
        "provenance": {"source": "test", "chunk_id": "chunk_001"},
        "project_id": "test_proj",
    }
    ku.update(overrides)
    return ku


def _make_config(backend):
    return RegisterKuConfig(backend=backend)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_valid_ku_registers_successfully():
    backend = MockBackend()
    ku = _make_ku()
    result = register_ku(_make_config(backend), {"ku": ku})
    assert result["status"] == "completed"
    assert result["findings"] is not None


def test_ku_id_returned_in_findings():
    backend = MockBackend()
    ku = _make_ku()
    result = register_ku(_make_config(backend), {"ku": ku})
    assert result["findings"] == "KU-test-001"


def test_fingerprint_computed_and_returned():
    backend = MockBackend()
    ku = _make_ku()
    result = register_ku(_make_config(backend), {"ku": ku})
    assert result["fingerprint"] is not None
    assert isinstance(result["fingerprint"], str)
    assert len(result["fingerprint"]) == 64  # sha256 hex


def test_decision_trail_returned_with_steps():
    backend = MockBackend()
    ku = _make_ku()
    result = register_ku(_make_config(backend), {"ku": ku})
    trail = result["decision_trail"]
    assert trail is not None
    assert "steps" in trail
    assert len(trail["steps"]) > 0


def test_missing_backend_returns_status_failed_no_raise():
    config = RegisterKuConfig(backend=None)
    ku = _make_ku()
    result = register_ku(config, {"ku": ku})
    assert result["status"] == "failed"
    assert result["error"] is not None
    assert result["findings"] is None


def test_missing_natural_text_returns_status_failed_no_raise():
    backend = MockBackend()
    ku = _make_ku(natural_text="")
    result = register_ku(_make_config(backend), {"ku": ku})
    assert result["status"] == "failed"
    assert result["findings"] is None


def test_missing_epistemic_status_returns_status_failed_no_raise():
    backend = MockBackend()
    ku = _make_ku()
    ku["epistemic_status"] = None
    result = register_ku(_make_config(backend), {"ku": ku})
    assert result["status"] == "failed"
    assert result["findings"] is None


def test_same_ku_twice_same_fingerprint():
    """Content-based fingerprint: same content → same fingerprint."""
    backend = MockBackend()
    ku1 = _make_ku(ku_id="KU-aaa")
    ku2 = _make_ku(ku_id="KU-bbb")  # different id, same content
    config = _make_config(backend)
    fp1 = compute_fingerprint_for(config, {"ku": ku1})
    fp2 = compute_fingerprint_for(config, {"ku": ku2})
    assert fp1 == fp2


def test_ku_stored_via_backend_put_node():
    backend = MockBackend()
    ku = _make_ku()
    register_ku(_make_config(backend), {"ku": ku})
    stored = backend.get_node("KU-test-001")
    assert stored is not None
    assert stored["natural_text"] == ku["natural_text"]


def test_output_dir_creates_decision_trail_json():
    backend = MockBackend()
    ku = _make_ku()
    with tempfile.TemporaryDirectory() as tmpdir:
        register_ku(_make_config(backend), {"ku": ku}, output_dir=tmpdir)
        trail_path = os.path.join(tmpdir, "decision_trail.json")
        assert os.path.exists(trail_path)
        with open(trail_path) as f:
            data = json.load(f)
        assert data["omodul"] == "register_ku"


def test_on_step_callback_invoked():
    backend = MockBackend()
    ku = _make_ku()
    steps_received = []
    register_ku(_make_config(backend), {"ku": ku}, on_step=steps_received.append)
    assert len(steps_received) > 0


def test_return_dict_has_all_standard_keys():
    backend = MockBackend()
    ku = _make_ku()
    result = register_ku(_make_config(backend), {"ku": ku})
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
