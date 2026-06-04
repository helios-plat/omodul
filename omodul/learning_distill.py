"""omodul.learning_distill — Distill Episode into solution_strategy KU and store.

3O layer: omodul (≥2 oprim: llm_distill_strategy + ku_gate_validate, transaction semantics).
13-Learning-SPEC: episodes with positive outcome get distilled into reusable strategies.
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
from oprim import llm_distill_strategy, ku_gate_validate

_enabled_pillars: set[str] = {"decision_trail"}


class LearningDistillConfig(BaseConfig):
    """learning_distill configuration."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    _omodul_name: ClassVar[str] = "learning_distill"
    _omodul_version: ClassVar[str] = "1.0.0"
    backend: Any = None


def learning_distill(
    config: LearningDistillConfig | dict,
    input_data: dict,
    output_dir: str | None = None,
    *,
    on_step: Callable | None = None,
) -> dict:
    """Distill an Episode into a solution_strategy KU and store it.

    config:     LearningDistillConfig (with backend)
    input_data: {
        "episode": {
            "event":   str,
            "outcome": str,
            "context": str | dict,
            ...
        }
    }
    output_dir: decision_trail.json write directory (None = no file)
    on_step:    per-step callback (optional)

    Returns standard omodul dict:
        findings: {
            ku_id:           str | None,
            status:          "stored" | "quarantined",
            validation_errors: list[str],
        } | None
        status:         "completed" | "failed"
        error:          failure reason (None on success)
        decision_trail: execution trail
        report_path:    None
        cost_usd:       0.0

    A19: distilled KU is always unverified (LLM proposes, never certifies).
    Failure does not raise (3O §5.12).
    """
    if isinstance(config, dict):
        config = LearningDistillConfig(**config) if config else LearningDistillConfig()

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

        episode = input_data.get("episode", {})
        if not episode:
            raise ValueError("episode is required in input_data")

        _emit({"step": "distill_strategy", "event": str(episode.get("event", ""))[:80]})

        # Step 1: call llm_distill_strategy to extract KU
        ku = llm_distill_strategy(episode=episode)
        _emit(
            {
                "step": "strategy_distilled",
                "knowledge_type": ku.get("knowledge_type"),
                "verified": ku.get("epistemic_status", {}).get("verified", False),
            }
        )

        # Step 2: validate KU via ku_gate_validate
        _emit({"step": "validate_ku"})
        validation = ku_gate_validate(ku=ku)
        _emit(
            {
                "step": "validation_result",
                "valid": validation["valid"],
                "errors": validation["errors"],
                "warnings": validation["warnings"],
            }
        )

        ku_id = ku.get("ku_id")

        if not validation["valid"]:
            # Quarantine: do not store, do not raise
            findings = {
                "ku_id": ku_id,
                "status": "quarantined",
                "validation_errors": validation["errors"],
            }
            status = "completed"
            _emit({"step": "quarantined", "ku_id": ku_id, "errors": validation["errors"]})
        else:
            # Step 3: store via backend
            _emit({"step": "store_ku", "ku_id": ku_id})
            backend.put_node(ku_id, ku)
            _emit({"step": "stored", "ku_id": ku_id})

            findings = {
                "ku_id": ku_id,
                "status": "stored",
                "validation_errors": [],
            }
            status = "completed"

    except Exception as e:
        error = {"code": "ERR_LEARNING_DISTILL", "message": str(e)}
        _emit({"step": "abort", "error": error})

    finally:
        decision_trail = {
            "omodul": "learning_distill",
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
