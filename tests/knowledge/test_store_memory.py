"""Tests for omodul.store_memory."""

from __future__ import annotations

import json
import os
import tempfile

import pytest

from omodul.store_memory import StoreMemoryConfig, store_memory


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

    def list_nodes(self):
        return list(self._nodes.values())


def _make_config(backend):
    return StoreMemoryConfig(backend=backend)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_store_query_memory_succeeds():
    backend = MockBackend()
    result = store_memory(
        _make_config(backend),
        {"memory_type": "query", "content": {"text": "What is AII?"}, "project_id": "p1"},
    )
    assert result["status"] == "completed"
    assert result["findings"] is not None


def test_store_case_memory_succeeds():
    backend = MockBackend()
    result = store_memory(
        _make_config(backend),
        {
            "memory_type": "case",
            "content": {"problem": "slow inference", "solution": "quantize model", "context": {}},
            "project_id": "p1",
        },
    )
    assert result["status"] == "completed"
    assert result["findings"] is not None


def test_store_solution_strategy_memory_succeeds():
    backend = MockBackend()
    result = store_memory(
        _make_config(backend),
        {
            "memory_type": "solution_strategy",
            "content": {
                "title": "Chunked retrieval",
                "description": "Split docs and retrieve per chunk",
                "content": "...",
            },
            "project_id": "p1",
        },
    )
    assert result["status"] == "completed"
    assert result["findings"] is not None


def test_invalid_memory_type_returns_failed_no_raise():
    backend = MockBackend()
    result = store_memory(
        _make_config(backend),
        {"memory_type": "unknown_type", "content": {}, "project_id": "p1"},
    )
    assert result["status"] == "failed"
    assert result["error"] is not None
    assert result["findings"] is None


def test_missing_backend_returns_failed_no_raise():
    config = StoreMemoryConfig(backend=None)
    result = store_memory(
        config,
        {"memory_type": "query", "content": {"text": "hello"}, "project_id": "p1"},
    )
    assert result["status"] == "failed"
    assert result["findings"] is None


def test_findings_contains_memory_id():
    backend = MockBackend()
    result = store_memory(
        _make_config(backend),
        {"memory_type": "query", "content": {"text": "What is knowledge?"}, "project_id": "p1"},
    )
    memory_id = result["findings"]
    assert memory_id is not None
    assert memory_id.startswith("MEM-")


def test_decision_trail_returned():
    backend = MockBackend()
    result = store_memory(
        _make_config(backend),
        {"memory_type": "query", "content": {"text": "test"}, "project_id": "p1"},
    )
    trail = result["decision_trail"]
    assert trail is not None
    assert "steps" in trail
    assert len(trail["steps"]) > 0


def test_fingerprint_returned():
    backend = MockBackend()
    result = store_memory(
        _make_config(backend),
        {"memory_type": "query", "content": {"text": "test query"}, "project_id": "p1"},
    )
    assert result["fingerprint"] is not None
    assert isinstance(result["fingerprint"], str)
    assert len(result["fingerprint"]) == 64  # sha256 hex


def test_return_dict_has_all_standard_keys():
    backend = MockBackend()
    result = store_memory(
        _make_config(backend),
        {"memory_type": "query", "content": {"text": "test"}, "project_id": "p1"},
    )
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


def test_on_step_callback_invoked():
    backend = MockBackend()
    steps = []
    store_memory(
        _make_config(backend),
        {"memory_type": "case", "content": {"problem": "p", "solution": "s"}, "project_id": "p1"},
        on_step=steps.append,
    )
    assert len(steps) > 0


def test_ku_stored_with_correct_knowledge_type():
    backend = MockBackend()
    result = store_memory(
        _make_config(backend),
        {
            "memory_type": "solution_strategy",
            "content": {"title": "T", "description": "D", "content": "C"},
            "project_id": "p1",
        },
    )
    memory_id = result["findings"]
    stored = backend.get_node(memory_id)
    assert stored is not None
    assert stored["knowledge_type"] == "solution_strategy"


def test_same_content_produces_same_fingerprint():
    backend1 = MockBackend()
    backend2 = MockBackend()
    content = {"memory_type": "query", "content": {"text": "identical"}, "project_id": "p1"}
    r1 = store_memory(_make_config(backend1), content)
    r2 = store_memory(_make_config(backend2), content)
    assert r1["fingerprint"] == r2["fingerprint"]


def test_output_dir_creates_decision_trail_json():
    backend = MockBackend()
    with tempfile.TemporaryDirectory() as tmpdir:
        store_memory(
            _make_config(backend),
            {"memory_type": "query", "content": {"text": "file test"}, "project_id": "p1"},
            output_dir=tmpdir,
        )
        trail_path = os.path.join(tmpdir, "decision_trail.json")
        assert os.path.exists(trail_path)
        with open(trail_path) as f:
            data = json.load(f)
        assert data["omodul"] == "store_memory"
