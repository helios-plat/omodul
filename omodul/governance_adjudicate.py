"""omodul.governance_adjudicate — L0-L4 tiered adjudication of governance decisions.

3O layer: omodul (≥2 oprim: coherence_compute + ku_gate_validate, transaction semantics).
00-Governance-SPEC: risk-tiered decision routing. High risk → escalate.
Pillar: {decision_trail}
"""

from __future__ import annotations

import os
import json
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, ClassVar

from pydantic import ConfigDict

from omodul._base_config import BaseConfig
from oprim import coherence_compute, ku_gate_validate

_enabled_pillars: set[str] = {"decision_trail"}

# L0: auto_approved, L1-L2: needs_review, L3-L4: escalate
_TIER_DECISION = {
    0: "auto_approved",
    1: "needs_review",
    2: "needs_review",
    3: "escalate",
    4: "escalate",
}

_TIER_LABEL = {
    0: "L0",
    1: "L1",
    2: "L2",
    3: "L3",
    4: "L4",
}

_TIER_JUSTIFICATION = {
    0: "L0: low risk — auto-approved per governance policy",
    1: "L1: moderate risk — owner review required",
    2: "L2: high risk — reviewer council required",
    3: "L3: expert risk — human domain expert review required",
    4: "L4: critical risk — external escalation required",
}


class GovernanceAdjudicateConfig(BaseConfig):
    """governance_adjudicate configuration."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    _omodul_name: ClassVar[str] = "governance_adjudicate"
    _omodul_version: ClassVar[str] = "1.0.0"
    backend: Any = None


def governance_adjudicate(
    config: GovernanceAdjudicateConfig | dict,
    input_data: dict,
    output_dir: str | None = None,
    *,
    on_step: Callable | None = None,
) -> dict:
    """L0-L4 tiered adjudication of governance decisions.

    config:     GovernanceAdjudicateConfig (with optional backend)
    input_data: {
        "action":         str,           # action being adjudicated
        "subject_ku_id":  str | None,    # KU subject of the action
        "evidence":       dict | None,   # evidence KU dict (optional)
        "risk_level":     int,           # 0-4
    }
    output_dir: decision_trail.json write directory (None = no file)
    on_step:    per-step callback (optional)

    Returns standard omodul dict:
        findings: {
            decision:            str,   # "auto_approved"|"needs_review"|"escalate"
            tier:                str,   # "L0".."L4"
            justification:       str,
            escalation_required: bool,
            validation_errors:   list[str],
            coherence_notes:     list[str],
        } | None
        status:         "completed" | "failed"
        error:          failure reason (None on success)
        decision_trail: execution trail
        report_path:    None
        cost_usd:       0.0

    Failure does not raise (3O §5.12).
    """
    if isinstance(config, dict):
        config = GovernanceAdjudicateConfig(**config) if config else GovernanceAdjudicateConfig()

    trail: list[dict] = []
    status = "failed"
    error = None
    findings = None

    def _emit(step_dict: dict) -> None:
        trail.append(step_dict)
        if on_step is not None:
            on_step(step_dict)

    try:
        action = input_data.get("action", "")
        subject_ku_id = input_data.get("subject_ku_id")
        evidence = input_data.get("evidence")
        risk_level = input_data.get("risk_level", 0)

        if not isinstance(risk_level, int) or risk_level not in range(5):
            raise ValueError(f"risk_level must be an integer 0-4, got: {risk_level!r}")

        _emit({"step": "adjudicate_start", "action": action, "risk_level": risk_level})

        validation_errors: list[str] = []
        coherence_notes: list[str] = []

        # Step 1: validate evidence KU if provided (ku_gate_validate)
        if evidence is not None:
            _emit({"step": "validate_evidence_ku"})
            val_result = ku_gate_validate(ku=evidence)
            validation_errors = val_result["errors"]
            _emit(
                {
                    "step": "evidence_validation",
                    "valid": val_result["valid"],
                    "errors": validation_errors,
                    "warnings": val_result["warnings"],
                }
            )

        # Step 2: coherence check if backend + subject_ku_id provided
        backend = config.backend
        if backend is not None and subject_ku_id is not None:
            _emit({"step": "coherence_check", "subject_ku_id": subject_ku_id})
            try:
                # Build minimal node/edge set from backend for coherence check
                nodes: dict = {}
                edges: list = []

                if hasattr(backend, "get_node"):
                    node = backend.get_node(subject_ku_id)
                    if node:
                        nodes[subject_ku_id] = node
                elif hasattr(backend, "nodes") and isinstance(backend.nodes, dict):
                    node = backend.nodes.get(subject_ku_id)
                    if node:
                        nodes[subject_ku_id] = node

                if hasattr(backend, "list_edges"):
                    edges = backend.list_edges()

                if nodes:
                    coherence_result = coherence_compute(nodes=nodes, edges=edges)
                    node_coherence = coherence_result.get(subject_ku_id, {})
                    contradictors = node_coherence.get("contradicts_from_confirmed", 0)
                    supporters = node_coherence.get("supports_from_confirmed", 0)
                    if contradictors > 0:
                        coherence_notes.append(
                            f"subject KU has {contradictors} confirmed contradictors — risk elevated"
                        )
                    if supporters > 0:
                        coherence_notes.append(f"subject KU has {supporters} confirmed supporters")
                    _emit(
                        {
                            "step": "coherence_result",
                            "supports_from_confirmed": supporters,
                            "contradicts_from_confirmed": contradictors,
                        }
                    )
            except Exception as ce:
                coherence_notes.append(f"coherence_check skipped: {ce}")
                _emit({"step": "coherence_skipped", "reason": str(ce)})
        else:
            _emit({"step": "coherence_skipped", "reason": "no backend or no subject_ku_id"})

        # Step 3: route by risk_level
        decision = _TIER_DECISION[risk_level]
        tier = _TIER_LABEL[risk_level]
        justification = _TIER_JUSTIFICATION[risk_level]
        escalation_required = risk_level >= 3

        _emit(
            {
                "step": "routing_decision",
                "risk_level": risk_level,
                "tier": tier,
                "decision": decision,
                "escalation_required": escalation_required,
            }
        )

        findings = {
            "decision": decision,
            "tier": tier,
            "justification": justification,
            "escalation_required": escalation_required,
            "validation_errors": validation_errors,
            "coherence_notes": coherence_notes,
        }
        status = "completed"

    except Exception as e:
        error = {"code": "ERR_GOVERNANCE_ADJUDICATE", "message": str(e)}
        _emit({"step": "abort", "error": error})

    finally:
        decision_trail = {
            "omodul": "governance_adjudicate",
            "enabled_pillars": sorted(_enabled_pillars),
            "status": status,
            "steps": trail,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
            with open(os.path.join(output_dir, "decision_trail.json"), "w", encoding="utf-8") as f:
                json.dump(decision_trail, f, ensure_ascii=False, indent=2)

    return {
        "findings": findings,
        "status": status,
        "error": error,
        "decision_trail": decision_trail,
        "report_path": None,
        "cost_usd": 0.0,
    }
