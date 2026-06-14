"""Tests for omodul.create_session (sync module)."""
from __future__ import annotations

import uuid

import pytest

from omodul.create_session import Config, InputData, create_session, compute_fingerprint_for


# ---------------------------------------------------------------------------
# 1. Returns status="completed"
# ---------------------------------------------------------------------------
def test_returns_completed(tmp_path):
    inp = InputData(title="My Session")
    result = create_session(Config(), inp, tmp_path)
    assert result["status"] == "completed"


# ---------------------------------------------------------------------------
# 2. session_id is a UUID string
# ---------------------------------------------------------------------------
def test_session_id_is_uuid(tmp_path):
    result = create_session(Config(), InputData(), tmp_path)
    sid = result["session_id"]
    parsed = uuid.UUID(sid)  # raises if invalid
    assert str(parsed) == sid


# ---------------------------------------------------------------------------
# 3. Unique IDs across calls
# ---------------------------------------------------------------------------
def test_unique_ids_across_calls(tmp_path):
    r1 = create_session(Config(), InputData(), tmp_path)
    r2 = create_session(Config(), InputData(), tmp_path)
    assert r1["session_id"] != r2["session_id"]


# ---------------------------------------------------------------------------
# 4. compute_fingerprint_for is deterministic
# ---------------------------------------------------------------------------
def test_compute_fingerprint_for_deterministic():
    cfg = Config(initial_model="claude-sonnet-4-6", agent_type="build")
    inp = InputData(initial_model="claude-sonnet-4-6", agent_type="build")
    fp1 = compute_fingerprint_for(cfg, inp)
    fp2 = compute_fingerprint_for(cfg, inp)
    assert fp1 == fp2


# ---------------------------------------------------------------------------
# 5. compute_fingerprint_for changes with different model
# ---------------------------------------------------------------------------
def test_compute_fingerprint_for_changes_with_model():
    cfg = Config()
    inp1 = InputData(initial_model="claude-sonnet-4-6")
    inp2 = InputData(initial_model="claude-haiku-4-5")
    assert compute_fingerprint_for(cfg, inp1) != compute_fingerprint_for(cfg, inp2)


# ---------------------------------------------------------------------------
# 6. Different title → same fingerprint (title not in fingerprint_fields)
# ---------------------------------------------------------------------------
def test_title_does_not_affect_fingerprint():
    cfg = Config()
    inp1 = InputData(title="Session Alpha", initial_model="claude-sonnet-4-6", agent_type="build")
    inp2 = InputData(title="Session Beta", initial_model="claude-sonnet-4-6", agent_type="build")
    assert compute_fingerprint_for(cfg, inp1) == compute_fingerprint_for(cfg, inp2)


# ---------------------------------------------------------------------------
# 7. Returns expected keys
# ---------------------------------------------------------------------------
def test_return_keys_present(tmp_path):
    result = create_session(Config(), InputData(), tmp_path)
    for key in ("status", "error", "fingerprint", "session_id", "session"):
        assert key in result, f"missing key: {key}"


# ---------------------------------------------------------------------------
# 8. on_step=None works — no error
# ---------------------------------------------------------------------------
def test_on_step_none_no_error(tmp_path):
    result = create_session(Config(), InputData(), tmp_path, on_step=None)
    assert result["status"] == "completed"


# ---------------------------------------------------------------------------
# 9. title default — auto-generated when empty
# ---------------------------------------------------------------------------
def test_title_default_auto_generated(tmp_path):
    result = create_session(Config(), InputData(title=""), tmp_path)
    session = result["session"]
    assert session["title"].startswith("Session ")


# ---------------------------------------------------------------------------
# 10. Explicit title is used when provided
# ---------------------------------------------------------------------------
def test_explicit_title_used(tmp_path):
    result = create_session(Config(), InputData(title="My Project"), tmp_path)
    assert result["session"]["title"] == "My Project"


# ---------------------------------------------------------------------------
# 11. fingerprint_fields: agent_type changes fingerprint
# ---------------------------------------------------------------------------
def test_agent_type_changes_fingerprint():
    cfg = Config()
    inp1 = InputData(agent_type="build")
    inp2 = InputData(agent_type="review")
    assert compute_fingerprint_for(cfg, inp1) != compute_fingerprint_for(cfg, inp2)


# ---------------------------------------------------------------------------
# 12. session dict contains expected fields
# ---------------------------------------------------------------------------
def test_session_dict_fields(tmp_path):
    result = create_session(Config(), InputData(title="T"), tmp_path)
    session = result["session"]
    for key in ("id", "title", "model", "agent_type", "history", "created_at"):
        assert key in session, f"session missing key: {key}"


# ---------------------------------------------------------------------------
# 13. Session model falls back to config.initial_model when not in InputData
# ---------------------------------------------------------------------------
def test_session_model_fallback_to_config(tmp_path):
    cfg = Config(initial_model="claude-haiku-4-5")
    inp = InputData()  # initial_model=""
    result = create_session(cfg, inp, tmp_path)
    assert result["session"]["model"] == "claude-haiku-4-5"
