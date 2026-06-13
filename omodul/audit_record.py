"""omodul.audit_record — Tamper-evident audit record with Ed25519 signature.

Pillars: decision_trail
Composites: obase.canonical_json + obase.sha256_hash + oprim.ed25519_sign

⚠️  Fingerprint covers event body ONLY — tier and signature fields excluded.
⚠️  canonical_json path must be identical for write and verify.
"""
from __future__ import annotations

import base64
from typing import Any, ClassVar

from omodul._base import BaseConfig, Trail, build_result


class AuditRecordConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "audit_record"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail"}
    _fingerprint_fields: ClassVar[set[str]] = {"event_type", "actor_id"}

    event_type: str
    actor_id: str = ""
    tier: str = "standard"
    private_key_b64: str = ""


def audit_record(
    event_body: dict[str, Any],
    *,
    config: AuditRecordConfig,
    store: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a signed audit record for *event_body*.

    Fingerprint = SHA-256(canonical_json(event_body)).
    tier and sig fields are excluded from fingerprint computation.
    The same canonical_json call is used for write and verify.

    Args:
        event_body: Arbitrary dict describing the audited event.
        config: AuditRecordConfig.
        store: Optional dict to persist the record (for tests).

    Returns:
        Result dict with ``record_id``, ``fingerprint_hex``, ``sig_b64``,
        ``tier``, ``body``.
    """
    from obase.canonical_json import canonical_json  # noqa: PLC0415
    from obase.sha256_hash import sha256_hash  # noqa: PLC0415

    trail = Trail()

    # Same canonical_json path for write and verify — body only, no tier/sig
    body_bytes: bytes = canonical_json(event_body)
    fingerprint: bytes = sha256_hash(body_bytes)
    fingerprint_hex: str = fingerprint.hex()
    trail.record(event="fingerprint_computed", fingerprint_hex=fingerprint_hex)

    sig_b64 = ""
    if config.private_key_b64:
        from oprim.ed25519_sign import ed25519_sign  # noqa: PLC0415

        private_key_bytes = base64.b64decode(config.private_key_b64)
        sig_bytes = ed25519_sign(fingerprint, private_key=private_key_bytes)
        sig_b64 = base64.b64encode(sig_bytes).decode()
        trail.record(event="signed")

    record_id = fingerprint_hex[:16]
    record = {
        "event_type": config.event_type,
        "actor_id": config.actor_id,
        "body": event_body,
        "fingerprint_hex": fingerprint_hex,
        "sig_b64": sig_b64,
        "tier": config.tier,  # tier excluded from fingerprint
    }

    if store is not None:
        store[record_id] = record

    trail.record(event="record_created", record_id=record_id)

    return build_result(
        status="ok",
        trail=trail,
        cost_usd=0.0,
        record_id=record_id,
        fingerprint_hex=fingerprint_hex,
        sig_b64=sig_b64,
        tier=config.tier,
        body=event_body,
    )


def audit_verify(
    record: dict[str, Any],
    *,
    public_key_b64: str = "",
) -> dict[str, Any]:
    """Verify integrity of a record produced by *audit_record*.

    Uses the identical canonical_json path as the write side.
    """
    from obase.canonical_json import canonical_json  # noqa: PLC0415
    from obase.sha256_hash import sha256_hash  # noqa: PLC0415

    body = record.get("body", {})
    recomputed = sha256_hash(canonical_json(body)).hex()
    fingerprint_match = recomputed == record.get("fingerprint_hex", "")

    sig_valid: bool | None = None
    if public_key_b64 and record.get("sig_b64"):
        from cryptography.exceptions import InvalidSignature  # noqa: PLC0415
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # noqa: PLC0415
            Ed25519PublicKey,
        )
        try:
            pub = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64))
            sig = base64.b64decode(record["sig_b64"])
            pub.verify(sig, bytes.fromhex(record["fingerprint_hex"]))
            sig_valid = True
        except InvalidSignature:
            sig_valid = False

    return {
        "valid": fingerprint_match and (sig_valid is not False),
        "fingerprint_match": fingerprint_match,
        "sig_valid": sig_valid,
    }
