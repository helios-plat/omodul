"""omodul.reuse_strategy — Match new problem to stored strategy, return reuse decision.

3O layer: omodul (≥2 operations: similarity search + epistemic state check).
A22 constraint: k=1 match, check epistemic status (proven→use / failed→avoid).
Pillars: {decision_trail}
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, ClassVar

from pydantic import ConfigDict

from omodul._base_config import BaseConfig

_enabled_pillars: set[str] = {"decision_trail"}

# Grades that positively recommend reuse
_POSITIVE_GRADES = {"proven", "high"}
# Grades that recommend avoiding
_NEGATIVE_GRADES = {"failed"}


def _token_overlap_score(query: str, text: str) -> float:
    """Simple token overlap similarity (no LLM needed per spec)."""
    if not query or not text:
        return 0.0
    q_tokens = set(query.lower().split())
    t_tokens = set(text.lower().split())
    if not q_tokens:
        return 0.0
    intersection = q_tokens & t_tokens
    # Jaccard-style: intersection / union
    union = q_tokens | t_tokens
    return len(intersection) / len(union) if union else 0.0


class ReuseStrategyConfig(BaseConfig):
    """reuse_strategy configuration."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    _omodul_name: ClassVar[str] = "reuse_strategy"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = set()
    backend: Any = None  # StorageBackend


def reuse_strategy(
    config: ReuseStrategyConfig | dict,
    input_data: dict,
    output_dir: str | None = None,
    *,
    on_step: Callable | None = None,
) -> dict:
    """Match new problem to stored solution_strategy KUs; return reuse decision.

    config:     ReuseStrategyConfig (with backend)
    input_data: {"query": str, "project_id": str (optional)}
    output_dir: decision_trail.json write directory (None = no file)
    on_step:    per-step callback (optional)

    Returns standard omodul dict:
        findings: {
            matched_ku_id:    str | None,
            similarity_score: float | None,
            recommend_reuse:  True | False | None,
            grade:            str | None,
            reason:           str,
        } | None (None if backend missing)
        status:         "completed" | "failed"
        error:          failure reason (None on success)
        decision_trail: execution trail
        report_path:    None
        cost_usd:       0.0

    A22: k=1 best match. recommend_reuse:
        proven/high  → True
        failed       → False (guard)
        else         → None (uncertain, human decision)
    """
    if isinstance(config, dict):
        config = ReuseStrategyConfig(**config) if config else ReuseStrategyConfig()
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

        query = input_data.get("query", "")

        # step 1: retrieve all solution_strategy KUs from backend
        _emit({"step": "retrieve_strategies"})
        all_strategies = []
        if hasattr(backend, "list_nodes_by_type"):
            all_strategies = backend.list_nodes_by_type("solution_strategy")
        elif hasattr(backend, "list_nodes"):
            all_strategies = [
                n for n in backend.list_nodes() if n.get("knowledge_type") == "solution_strategy"
            ]
        _emit({"step": "strategies_found", "count": len(all_strategies)})

        if not all_strategies:
            findings = {
                "matched_ku_id": None,
                "similarity_score": None,
                "recommend_reuse": None,
                "grade": None,
                "reason": "no_strategies_available",
            }
            status = "completed"
            _emit({"step": "no_strategies", "result": "completed_empty"})
        else:
            # step 2: k=1 best match by token overlap (A22: no LLM)
            best_ku = None
            best_score = -1.0
            for ku in all_strategies:
                text = ku.get("natural_text", "")
                score = _token_overlap_score(query, text)
                if score > best_score:
                    best_score = score
                    best_ku = ku

            _emit(
                {
                    "step": "similarity_match",
                    "matched_ku_id": best_ku.get("ku_id") if best_ku else None,
                    "similarity_score": best_score,
                }
            )

            # step 3: check epistemic grade (A22 guard)
            grade = None
            recommend_reuse = None
            reason = "uncertain"
            if best_ku:
                ep_status = best_ku.get("epistemic_status", {})
                grade = ep_status.get("grade") if isinstance(ep_status, dict) else None
                if grade in _POSITIVE_GRADES:
                    recommend_reuse = True
                    reason = f"grade={grade}: recommend reuse"
                elif grade in _NEGATIVE_GRADES:
                    recommend_reuse = False
                    reason = f"grade={grade}: avoid (failed strategy)"
                else:
                    recommend_reuse = None
                    reason = f"grade={grade}: uncertain, human decision required"

            _emit(
                {
                    "step": "epistemic_check",
                    "grade": grade,
                    "recommend_reuse": recommend_reuse,
                }
            )

            findings = {
                "matched_ku_id": best_ku.get("ku_id") if best_ku else None,
                "similarity_score": best_score,
                "recommend_reuse": recommend_reuse,
                "grade": grade,
                "reason": reason,
            }
            status = "completed"

    except Exception as e:
        error = {"code": "ERR_REUSE_STRATEGY", "message": str(e)}
        _emit({"step": "abort", "error": error})
    finally:
        decision_trail = {
            "omodul": "reuse_strategy",
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
