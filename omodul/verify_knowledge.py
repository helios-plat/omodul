"""omodul.verify_knowledge — Verify a KU and update its epistemic grade.

3O layer: omodul (≥2 oprim: cmi_verify + backtest_stat, business transaction semantics).
A20 defeasible: grade upgrades/downgrades based on verification evidence.
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
from oprim import cmi_verify, backtest_stat

_enabled_pillars: set[str] = {"decision_trail"}

GRADE_LADDER = ["unverified", "low", "moderate", "high", "proven"]
# Auto-verify can upgrade at most to "high"; "proven" requires formal proof
_AUTO_VERIFY_CEILING = "high"


def _grade_index(g: str) -> int:
    return GRADE_LADDER.index(g) if g in GRADE_LADDER else 0


def _grade_up(current: str) -> str:
    """Upgrade one step, capped at _AUTO_VERIFY_CEILING."""
    idx = _grade_index(current)
    ceiling_idx = _grade_index(_AUTO_VERIFY_CEILING)
    new_idx = min(idx + 1, ceiling_idx)
    return GRADE_LADDER[new_idx]


def _grade_down(current: str) -> str:
    """Downgrade one step, floored at 'unverified'."""
    idx = _grade_index(current)
    return GRADE_LADDER[max(idx - 1, 0)]


class VerifyKnowledgeConfig(BaseConfig):
    """verify_knowledge configuration."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    _omodul_name: ClassVar[str] = "verify_knowledge"
    _omodul_version: ClassVar[str] = "1.0.0"
    backend: Any = None


def verify_knowledge(
    config: VerifyKnowledgeConfig | dict,
    input_data: dict,
    output_dir: str | None = None,
    *,
    on_step: Callable | None = None,
) -> dict:
    """Verify a KU and update its epistemic grade based on evidence.

    config:     VerifyKnowledgeConfig (with backend)
    input_data: {
        "ku_id": str,
        "verification_data": {
            "type": "cmi" | "backtest" | "manual",
            "treatment": list | None,   # for cmi
            "control":   list | None,   # for cmi
            "returns":   list | None,   # for backtest
            "verdict":   "confirmed" | "refuted" | None  # for manual
        }
    }
    output_dir: decision_trail.json write directory (None = no file)
    on_step:    per-step callback (optional)

    Returns standard omodul dict:
        findings: {ku_id, old_grade, new_grade, action} | None
        status:   "completed" | "failed"
        error:    failure reason (None on success)
        decision_trail: execution trail
        report_path: None
        cost_usd: 0.0

    A20 defeasible: grade upgrades/downgrades. "proven" not reachable from auto-verify.
    Failure does not raise (3O §5.12).
    """
    if isinstance(config, dict):
        config = VerifyKnowledgeConfig(**config) if config else VerifyKnowledgeConfig()

    trail: list[dict] = []
    status = "failed"
    error = None
    findings = None

    def _emit(step_dict: dict) -> None:
        trail.append(step_dict)
        if on_step is not None:
            on_step(step_dict)

    try:
        backend = config.backend
        if backend is None:
            raise ValueError("backend is required but not provided")

        ku_id = input_data.get("ku_id")
        if not ku_id:
            raise ValueError("ku_id is required in input_data")

        verification_data = input_data.get("verification_data", {})
        vtype = verification_data.get("type", "manual")

        _emit({"step": "load_ku", "ku_id": ku_id})

        # Retrieve KU from backend
        ku = None
        if hasattr(backend, "get_node"):
            ku = backend.get_node(ku_id)
        elif hasattr(backend, "nodes") and isinstance(backend.nodes, dict):
            ku = backend.nodes.get(ku_id)

        if ku is None:
            raise ValueError(f"KU not found: {ku_id}")

        _emit({"step": "ku_loaded", "ku_id": ku_id})

        # Read current grade
        ep_status = ku.get("epistemic_status", {})
        if not isinstance(ep_status, dict):
            ep_status = {}
        old_grade = ep_status.get("grade", "unverified")
        _emit({"step": "current_grade", "grade": old_grade})

        # Run appropriate verification oprim
        action = "no_change"
        new_grade = old_grade
        evidence_summary: dict = {}

        if vtype == "cmi":
            treatment = verification_data.get("treatment") or []
            control = verification_data.get("control") or []
            _emit(
                {"step": "run_cmi_verify", "n_treatment": len(treatment), "n_control": len(control)}
            )
            cmi_result = cmi_verify(treatment=treatment, control=control)
            evidence_summary = cmi_result
            _emit(
                {
                    "step": "cmi_result",
                    "significant": cmi_result["significant"],
                    "causal_confidence": cmi_result["causal_confidence"],
                }
            )

            cc = cmi_result["causal_confidence"]
            if cc in ("strong", "moderate"):
                new_grade = _grade_up(old_grade)
                action = "upgraded" if new_grade != old_grade else "capped"
            elif cc == "weak":
                # weak significant: no change
                action = "no_change"
            else:
                # not significant: no change (defeater needed for downgrade via CMI alone)
                action = "no_change"

        elif vtype == "backtest":
            returns = verification_data.get("returns") or []
            _emit({"step": "run_backtest_stat", "n_returns": len(returns)})
            bt_result = backtest_stat(returns=returns)
            evidence_summary = bt_result
            _emit({"step": "backtest_result", "sharpe_ratio": bt_result["sharpe_ratio"]})

            sr = bt_result["sharpe_ratio"]
            if sr > 1.0:
                new_grade = _grade_up(old_grade)
                action = "upgraded" if new_grade != old_grade else "capped"
            elif sr < 0:
                new_grade = _grade_down(old_grade)
                action = "downgraded" if new_grade != old_grade else "no_change"
            else:
                action = "no_change"

        elif vtype == "manual":
            verdict = verification_data.get("verdict")
            _emit({"step": "manual_verdict", "verdict": verdict})
            if verdict == "confirmed":
                new_grade = _grade_up(old_grade)
                action = "upgraded" if new_grade != old_grade else "capped"
            elif verdict == "refuted":
                new_grade = _grade_down(old_grade)
                action = "downgraded" if new_grade != old_grade else "no_change"
            else:
                action = "no_change"

        else:
            _emit({"step": "unknown_verification_type", "type": vtype})
            action = "no_change"

        # Persist updated grade if changed
        if new_grade != old_grade:
            ku["epistemic_status"] = dict(ep_status)
            ku["epistemic_status"]["grade"] = new_grade
            if hasattr(backend, "put_node"):
                backend.put_node(ku_id, ku)
            _emit({"step": "grade_updated", "old_grade": old_grade, "new_grade": new_grade})

        findings = {
            "ku_id": ku_id,
            "old_grade": old_grade,
            "new_grade": new_grade,
            "action": action,
            "evidence_summary": evidence_summary,
        }
        status = "completed"
        _emit({"step": "done", "action": action})

    except Exception as e:
        error = {"code": "ERR_VERIFY_KNOWLEDGE", "message": str(e)}
        _emit({"step": "abort", "error": error})

    finally:
        decision_trail = {
            "omodul": "verify_knowledge",
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
