"""omodul.register_ku — Register a Knowledge Unit per HOS-001 three-face schema.

3O layer: omodul (≥2 operations: validation + storage, business transaction semantics).
Pillars: {fingerprint, decision_trail}
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, ClassVar

from pydantic import ConfigDict

from omodul._base_config import BaseConfig

_enabled_pillars: set[str] = {"fingerprint", "decision_trail"}


def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False)


def _sha256_hash(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


class RegisterKuConfig(BaseConfig):
    """register_ku configuration."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    _omodul_name: ClassVar[str] = "register_ku"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {"knowledge_type", "natural_text"}
    backend: Any = None  # StorageBackend


def compute_fingerprint_for(config: RegisterKuConfig | dict, input_data: dict) -> str:
    """Public function (3O §5.11): compute content fingerprint for a KU.

    Fingerprint = sha256(canonical_json({knowledge_type, natural_text})).
    Content-based: same KU content → same fingerprint regardless of ku_id.
    """
    if isinstance(config, dict):
        config = RegisterKuConfig(**config) if config else RegisterKuConfig()
    ku = input_data["ku"]
    basis = {
        "knowledge_type": ku.get("knowledge_type"),
        "natural_text": ku.get("natural_text", ""),
    }
    return _sha256_hash(_canonical_json(basis))


def register_ku(
    config: RegisterKuConfig | dict,
    input_data: dict,
    output_dir: str | None = None,
    *,
    on_step: Callable | None = None,
) -> dict:
    """Register a KU per HOS-001 three-face schema.

    config:     RegisterKuConfig (with backend)
    input_data: {"ku": KU dict (from llm_extract_ku or similar)}
    output_dir: decision_trail.json write directory (None = no file, trail still returned)
    on_step:    per-step callback (optional)

    Returns standard omodul dict:
        findings:       ku_id (None on failure)
        status:         "completed" | "failed"
        error:          failure reason (None on success)
        fingerprint:    content fingerprint (fingerprint pillar)
        decision_trail: execution trail (decision_trail pillar)
        report_path:    None (not enabled)
        cost_usd:       0.0 (no LLM)

    Failure does not raise (3O §5.12).
    """
    if isinstance(config, dict):
        config = RegisterKuConfig(**config) if config else RegisterKuConfig()
    trail: list[dict] = []
    fingerprint: str | None = None
    ku_id = None
    status = "failed"
    error = None

    def _emit(step_dict: dict) -> None:
        trail.append(step_dict)
        if on_step is not None:
            on_step(step_dict)

    try:
        backend = config.backend
        if backend is None:
            raise ValueError("backend is required but not provided")

        ku = input_data["ku"]
        ku_id = ku.get("ku_id") or f"KU-{uuid.uuid4()}"

        # step 1: validate required fields
        _emit({"step": "validate_ku_fields"})
        missing = []
        if not ku.get("natural_text", "").strip():
            missing.append("natural_text")
        if not ku.get("epistemic_status"):
            missing.append("epistemic_status")
        if missing:
            raise ValueError(f"KU missing required fields: {missing}")
        trail[-1]["result"] = "pass"

        # step 2: compute content fingerprint (fingerprint pillar)
        fingerprint = compute_fingerprint_for(config=config, input_data=input_data)
        _emit({"step": "compute_fingerprint", "fingerprint": fingerprint})

        # step 3: store KU via backend
        ku["ku_id"] = ku_id
        backend.put_node(ku_id, ku)
        _emit({"step": "put_node", "ku_id": ku_id, "result": "ok"})

        status = "completed"
    except Exception as e:
        error = {"code": "ERR_REGISTER_KU", "message": str(e)}
        _emit({"step": "abort", "error": error})
    finally:
        decision_trail = {
            "omodul": "register_ku",
            "enabled_pillars": sorted(_enabled_pillars),
            "ku_id": ku_id,
            "status": status,
            "steps": trail,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
            with open(os.path.join(output_dir, "decision_trail.json"), "w", encoding="utf-8") as f:
                json.dump(decision_trail, f, ensure_ascii=False, indent=2)

    return {
        "findings": ku_id if status == "completed" else None,
        "status": status,
        "error": error,
        "fingerprint": fingerprint,
        "decision_trail": decision_trail,
        "report_path": None,
        "cost_usd": 0.0,
    }
