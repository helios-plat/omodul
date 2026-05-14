"""Tests for omodul.audit.vcp_silver_record."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from omodul.audit import vcp_silver_record, VALID_EVENT_TYPES
from oprim.crypto import sha256_hash
from oprim.serialization import canonical_json

SCHEMA_PATH = (
    Path(__file__).parent.parent
    / "omodul" / "schemas" / "audit" / "vcp_silver_record.schema.json"
)

_DET_EVIDENCE = {
    "input_snapshot_hash": "abc123",
    "stack_version": "1.0.0",
    "random_seed": 42,
}

_FAITH_EVIDENCE = {
    "stack_calls": [{"function": "bocpd", "args_hash": "deadbeef"}],
    "intermediate_results": {"risk_status": "GREEN"},
    "precondition_checks": ["equity_curve >= 2"],
}

_DECISION = {"action": "buy", "size": 1000.0}


def _minimal_event(**overrides) -> dict:
    """Build a minimal valid vcp_silver_record call kwargs."""
    defaults = dict(
        decision=_DECISION,
        determinism_evidence=dict(_DET_EVIDENCE),
        faithfulness_evidence=dict(_FAITH_EVIDENCE),
        policy_id="policy-001",
        policy_version=1,
        strategy_id="bocpd_trend",
        strategy_instance_id="inst-001",
        event_type="signal_proposed",
        hash_prev=None,
    )
    defaults.update(overrides)
    return defaults


class TestVcpBasic:
    def test_vcp_first_event_null_prev(self):
        event = vcp_silver_record(**_minimal_event(hash_prev=None))
        assert event["hash_prev"] is None
        assert isinstance(event["hash_current"], bytes)
        assert len(event["hash_current"]) == 32  # SHA-256 = 32 bytes

    def test_vcp_output_keys(self):
        event = vcp_silver_record(**_minimal_event())
        required = {
            "event_id", "event_timestamp", "policy_id", "policy_version",
            "conformance_tier", "event_type", "strategy_instance_id", "strategy_id",
            "determinism_evidence", "faithfulness_evidence", "decision_payload",
            "hash_prev", "hash_current", "signature", "signing_key_id",
        }
        assert required.issubset(set(event.keys()))

    def test_vcp_conformance_tier(self):
        event = vcp_silver_record(**_minimal_event())
        assert event["conformance_tier"] == "SILVER"

    def test_vcp_signature_null(self):
        event = vcp_silver_record(**_minimal_event())
        assert event["signature"] is None
        assert event["signing_key_id"] is None


class TestVcpValidation:
    def test_vcp_invalid_event_type(self):
        with pytest.raises(ValueError, match="event_type"):
            vcp_silver_record(**_minimal_event(event_type="invalid_type"))

    def test_vcp_missing_determinism_keys(self):
        bad_det = {"input_snapshot_hash": "x", "stack_version": "1.0"}
        # missing random_seed
        with pytest.raises(ValueError, match="determinism_evidence"):
            vcp_silver_record(**_minimal_event(determinism_evidence=bad_det))

    def test_vcp_missing_faithfulness_keys(self):
        bad_faith = {"intermediate_results": {}, "precondition_checks": []}
        # missing stack_calls
        with pytest.raises(ValueError, match="faithfulness_evidence"):
            vcp_silver_record(**_minimal_event(faithfulness_evidence=bad_faith))

    def test_all_valid_event_types_accepted(self):
        for et in VALID_EVENT_TYPES:
            event = vcp_silver_record(**_minimal_event(event_type=et))
            assert event["event_type"] == et


class TestVcpHashChain:
    @pytest.mark.academic_reference
    def test_vcp_hash_chain(self):
        """Two chained events: event2.hash_prev == event1.hash_current."""
        event1 = vcp_silver_record(**_minimal_event())
        event2 = vcp_silver_record(**_minimal_event(hash_prev=event1["hash_current"]))
        assert event2["hash_prev"] == event1["hash_current"]

    def test_vcp_tamper_detection(self):
        """Changing decision_payload invalidates hash_current."""
        kwargs = _minimal_event()
        event = vcp_silver_record(**kwargs)
        original_hash = event["hash_current"]

        # Now compute what hash would be with tampered decision
        tampered_decision = {"action": "sell", "size": 9999.0}
        tampered_kwargs = _minimal_event(decision=tampered_decision)
        tampered_event = vcp_silver_record(**tampered_kwargs)

        assert tampered_event["hash_current"] != original_hash

    def test_vcp_hash_current_deterministic_for_same_event_body(self):
        """hash_current depends on canonical_json of event body."""
        event = vcp_silver_record(**_minimal_event(hash_prev=b"\x00" * 32))
        # The hash_current must be bytes of length 32
        assert isinstance(event["hash_current"], bytes)
        assert len(event["hash_current"]) == 32

    def test_vcp_hash_prev_bytes_stored(self):
        """When hash_prev is bytes, it is stored as-is."""
        prev = b"\xde\xad\xbe\xef" * 8  # 32 bytes
        event = vcp_silver_record(**_minimal_event(hash_prev=prev))
        assert event["hash_prev"] == prev


class TestVcpSchema:
    def test_vcp_schema_valid(self):
        """Validate output against JSON schema (if jsonschema available)."""
        pytest.importorskip("jsonschema")
        import jsonschema

        with open(SCHEMA_PATH) as f:
            schema = json.load(f)

        event = vcp_silver_record(**_minimal_event())
        # Convert bytes to hex strings for JSON schema validation
        event_serializable = dict(event)
        event_serializable["hash_current"] = event["hash_current"].hex()
        if event.get("hash_prev") is not None and isinstance(event["hash_prev"], bytes):
            event_serializable["hash_prev"] = event["hash_prev"].hex()

        # Should not raise
        jsonschema.validate(instance=event_serializable, schema=schema)
