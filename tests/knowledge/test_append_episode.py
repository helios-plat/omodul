"""Tests for omodul.append_episode."""

from __future__ import annotations

import json
import os

import pytest

from omodul.append_episode import AppendEpisodeConfig, append_episode

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


def make_config(backend=None):
    return AppendEpisodeConfig(backend=backend or MockBackend())


def valid_episode(**overrides):
    ep = {
        "project_id": "PRJ-001",
        "event": "model_retrained",
        "outcome": "accuracy improved from 0.82 to 0.87",
        "env_fingerprint": "fp-abc123",
    }
    ep.update(overrides)
    return {"episode": ep}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_valid_episode_returns_completed():
    """Valid episode appended successfully."""
    config = make_config()
    result = append_episode(config, valid_episode())
    assert result["status"] == "completed"
    assert result["error"] is None


def test_episode_id_returned_in_findings():
    """findings contains the episode_id on success."""
    config = make_config()
    result = append_episode(config, valid_episode())
    assert result["findings"] is not None
    assert result["findings"].startswith("EP-")


def test_explicit_episode_id_preserved():
    """If episode_id is supplied in input, it is used as-is."""
    config = make_config()
    input_data = valid_episode(episode_id="EP-CUSTOM-99")
    result = append_episode(config, input_data)
    assert result["findings"] == "EP-CUSTOM-99"
    assert result["status"] == "completed"


def test_decision_trail_returned():
    """decision_trail is returned and has correct omodul name."""
    config = make_config()
    result = append_episode(config, valid_episode())
    trail = result["decision_trail"]
    assert trail["omodul"] == "append_episode"
    assert trail["status"] == "completed"
    assert "decision_trail" in trail["enabled_pillars"]


def test_missing_project_id_fails_gracefully():
    """Episode without project_id returns status=failed, no raise."""
    config = make_config()
    input_data = valid_episode(project_id=None)
    result = append_episode(config, input_data)
    assert result["status"] == "failed"
    assert result["findings"] is None
    assert result["error"]["code"] == "ERR_MEMORY_ORPHANED"


def test_missing_env_fingerprint_fails_gracefully():
    """Episode without env_fingerprint returns status=failed, no raise."""
    config = make_config()
    ep = {
        "project_id": "PRJ-001",
        "event": "test_run",
        "outcome": "ok",
        # env_fingerprint intentionally absent
    }
    result = append_episode(config, {"episode": ep})
    assert result["status"] == "failed"
    assert result["findings"] is None
    assert result["error"]["code"] == "ERR_MEMORY_ORPHANED"


def test_episode_stored_with_put_node():
    """After successful append, node is stored in backend."""
    backend = MockBackend()
    config = make_config(backend=backend)
    result = append_episode(config, valid_episode(episode_id="EP-STORE-1"))
    assert result["status"] == "completed"
    stored = backend.get_node("EP-STORE-1")
    assert stored is not None
    assert stored["type"] == "Episode"
    assert stored["event"] == "model_retrained"


def test_belongs_to_edge_created():
    """A belongs_to edge from episode to project is created."""
    backend = MockBackend()
    config = make_config(backend=backend)
    result = append_episode(config, valid_episode(episode_id="EP-EDGE-1", project_id="PRJ-XYZ"))
    assert result["status"] == "completed"
    assert ("EP-EDGE-1", "belongs_to", "PRJ-XYZ") in backend._edges


def test_output_dir_creates_decision_trail_json(tmp_path):
    """When output_dir is provided, decision_trail.json is written to disk."""
    config = make_config()
    out_dir = str(tmp_path / "episode_trails")
    result = append_episode(config, valid_episode(), output_dir=out_dir)
    trail_file = os.path.join(out_dir, "decision_trail.json")
    assert os.path.exists(trail_file)
    with open(trail_file) as f:
        on_disk = json.load(f)
    assert on_disk["omodul"] == "append_episode"
    assert on_disk["status"] == "completed"


def test_on_step_callback_called():
    """on_step is invoked for each trail step emitted."""
    config = make_config()
    seen = []
    append_episode(config, valid_episode(), on_step=lambda s: seen.append(s["step"]))
    assert "check_non_orphan" in seen
    assert "check_env_fingerprint" in seen
    assert "store_episode" in seen


def test_return_dict_has_all_required_keys():
    """Result contains all six required keys."""
    config = make_config()
    result = append_episode(config, valid_episode())
    for key in ("findings", "status", "error", "decision_trail", "report_path", "cost_usd"):
        assert key in result, f"Missing key: {key}"
    assert result["report_path"] is None
    assert result["cost_usd"] == 0.0


def test_context_merged_into_stored_episode():
    """Extra context fields are merged under episode.context."""
    backend = MockBackend()
    config = make_config(backend=backend)
    ep = {
        "project_id": "PRJ-CTX",
        "event": "eval",
        "outcome": "pass",
        "env_fingerprint": "fp-ctx",
        "context": {"dataset": "v3", "seed": 42},
        "episode_id": "EP-CTX-1",
    }
    result = append_episode(config, {"episode": ep})
    assert result["status"] == "completed"
    stored = backend.get_node("EP-CTX-1")
    assert stored["context"]["dataset"] == "v3"
    assert stored["context"]["seed"] == 42
    assert stored["context"]["env_fingerprint"] == "fp-ctx"


def test_decision_trail_steps_on_failure_contain_abort():
    """Failed append trail contains an abort step."""
    config = make_config()
    input_data = valid_episode(project_id="")
    result = append_episode(config, input_data)
    steps = result["decision_trail"]["steps"]
    step_names = [s["step"] for s in steps]
    assert "abort" in step_names
