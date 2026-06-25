"""M-animation_workflow: orchestrate animation generation + optional persistence.

Pillars: fingerprint, decision_trail
Fingerprint fields: entity_id, domain

Flow:
  1. generate_animation (oskill) → AnimationResult
  2. db_writer (injected by Layer4) → persist when provided
  3. decision_trail written to output_dir

db_writer is a plain callable(dict) injected by Layer4 (each project's DB differs).
None=no persistence, result only.  Never raises.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from oprim._animation_types import AnimationInput, AnimationResult

from omodul._base import (
    BaseConfig, Trail, build_result, compute_fingerprint,
)

from oskill._generate_animation import generate_animation


class AnimationConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "animation_workflow"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set] = {"fingerprint", "decision_trail"}
    _fingerprint_fields: ClassVar[set] = {"entity_id", "domain"}

    entity_id: str
    domain: str


async def animation_workflow(
    config: AnimationConfig,
    input_data: AnimationInput,
    output_dir: Path,
    *,
    llm,
    db_writer: Any = None,
    on_step: Any = None,
) -> dict:
    """Orchestrate animation generation and optional persistence.

    llm:       LLMCaller injected by Layer4 (passed through to generate_animation)
    db_writer: optional callable(dict) — Layer4 supplies its own DB write logic.
               If None, result is returned without any persistence.
    on_step:   optional callback(step: str, state: str) for progress hooks.

    Returns standard omodul result dict (status / fingerprint / trail_path / …).
    Never raises — exceptions are caught, trail-recorded, returned as status=failed.
    """
    trail = Trail()
    fingerprint = compute_fingerprint({
        "entity_id": config.entity_id,
        "domain": config.domain,
    })

    _notify(on_step, "animation_workflow", "start")

    # ------------------------------------------------------------------
    # Generate
    # ------------------------------------------------------------------
    try:
        anim: AnimationResult = await generate_animation(
            template=input_data.template,
            variables=input_data.variables,
            domain_prompt=input_data.domain_prompt,
            llm=llm,
        )
    except Exception as exc:
        trail.record(event="generation_failed", error=str(exc), fingerprint=fingerprint)
        _notify(on_step, "animation_workflow", "failed")
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
            fingerprint=fingerprint,
            trail=trail,
            trail_path=None,
            cost_usd=0.0,
            html=None,
            is_valid=False,
            validation_violations=[],
            entity_id=config.entity_id,
            domain=config.domain,
            entity_meta={},
        )

    trail.record(
        event="generated",
        entity_id=config.entity_id,
        domain=config.domain,
        is_valid=anim.is_valid,
        validation_violations=anim.validation_violations,
        fingerprint=fingerprint,
    )

    # ------------------------------------------------------------------
    # Persist (optional)
    # ------------------------------------------------------------------
    if db_writer is not None:
        try:
            db_writer({
                "entity_id": config.entity_id,
                "domain": config.domain,
                "html": anim.html,
                "is_valid": anim.is_valid,
                "fingerprint": fingerprint,
            })
            trail.record(event="db_written", entity_id=config.entity_id)
        except Exception as exc:
            trail.record(event="db_write_failed", error=str(exc))

    _notify(on_step, "animation_workflow", "done")
    trail_path = trail.write(output_dir)

    return build_result(
        status="completed",
        error=None,
        fingerprint=fingerprint,
        trail=trail,
        trail_path=trail_path,
        cost_usd=0.0,
        html=anim.html,
        is_valid=anim.is_valid,
        validation_violations=anim.validation_violations,
        entity_id=config.entity_id,
        domain=config.domain,
        entity_meta=anim.entity_meta,
    )


def _notify(on_step: Any, step: str, state: str) -> None:
    if on_step is not None:
        try:
            on_step(step, state)
        except Exception:
            pass
