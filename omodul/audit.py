"""VCP audit event construction."""
from __future__ import annotations
import os
import time
from datetime import datetime, timezone

from oprim.crypto import sha256_hash
from oprim.serialization import canonical_json

def _native(obj):
    """Recursively coerce numpy scalars to native Python for canonical hashing."""
    if hasattr(obj, "item") and callable(obj.item):
        return obj.item()
    if isinstance(obj, dict):
        return {k: _native(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_native(v) for v in obj]
    return obj


VALID_EVENT_TYPES = {
    "signal_proposed", "signal_dropped",
    "order_approved", "risk_blocked",
    "order_submitted", "fill_received",
    "order_cancelled", "order_rejected",
    "mode_transition", "circuit_breaker_state_change",
}


def _uuid7() -> str:
    """UUIDv7 using stdlib only (time-ordered)."""
    ts_ms = int(time.time() * 1000) & 0xFFFFFFFFFFFF
    rand = int.from_bytes(os.urandom(10), 'big')
    rand_a = (rand >> 68) & 0x0FFF
    rand_b = rand & 0x3FFFFFFFFFFFFFFF
    value = (ts_ms << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b
    h = f'{value:032x}'
    return f'{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}'


def vcp_silver_record(
    decision: dict,
    determinism_evidence: dict,
    faithfulness_evidence: dict,
    policy_id: str,
    policy_version: int,
    strategy_id: str,
    strategy_instance_id: str,
    event_type: str,
    hash_prev: bytes | None,
) -> dict:
    """Construct VCP v1.1 SILVER tier audit event.

    Parameters
    ----------
    decision : dict
        Decision payload.
    determinism_evidence : dict
        Must include: input_snapshot_hash, stack_version, random_seed.
    faithfulness_evidence : dict
        Must include: stack_calls (list), intermediate_results, precondition_checks.
    policy_id : str
    policy_version : int
    strategy_id : str
    strategy_instance_id : str
    event_type : str
        One of the 10 valid event types.
    hash_prev : bytes | None
        SHA-256 of previous event's hash_current, or None for first event.

    Returns
    -------
    dict
        Complete VCP SILVER event with hash_current computed.

    Raises
    ------
    ValueError
        If event_type is invalid or required evidence keys are missing.

    References
    ----------
    .. [1] HELIVEX_AUDIT_SCHEMA.md v0.3 §5.
    .. [2] VeritasChain VCP v1.1 SILVER tier specification.
    """
    if event_type not in VALID_EVENT_TYPES:
        raise ValueError(
            f"event_type must be one of {sorted(VALID_EVENT_TYPES)}, got {event_type!r}"
        )

    required_det = {"input_snapshot_hash", "stack_version", "random_seed"}
    missing_det = required_det - set(determinism_evidence)
    if missing_det:
        raise ValueError(f"determinism_evidence missing keys: {missing_det}")

    required_faith = {"stack_calls", "intermediate_results", "precondition_checks"}
    missing_faith = required_faith - set(faithfulness_evidence)
    if missing_faith:
        raise ValueError(f"faithfulness_evidence missing keys: {missing_faith}")

    event = {
        "event_id": _uuid7(),
        "event_timestamp": datetime.now(timezone.utc).isoformat(),
        "policy_id": policy_id,
        "policy_version": policy_version,
        "conformance_tier": "SILVER",
        "event_type": event_type,
        "strategy_instance_id": strategy_instance_id,
        "strategy_id": strategy_id,
        "determinism_evidence": dict(determinism_evidence),
        "faithfulness_evidence": dict(faithfulness_evidence),
        "decision_payload": dict(decision),
    }

    canonical = canonical_json(_native(event))
    canonical_bytes = canonical.encode() if isinstance(canonical, str) else canonical
    if hash_prev is None:
        prev_bytes = b"\x00" * 32
    elif isinstance(hash_prev, str):
        prev_bytes = bytes.fromhex(hash_prev)
    else:
        prev_bytes = hash_prev
    hash_raw = sha256_hash(prev_bytes + canonical_bytes)
    hash_current = bytes.fromhex(hash_raw) if isinstance(hash_raw, str) else hash_raw

    event["hash_prev"] = hash_prev
    event["hash_current"] = hash_current
    event["signature"] = None
    event["signing_key_id"] = None

    return event
