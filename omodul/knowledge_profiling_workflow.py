"""omodul.knowledge_profiling_workflow — Build student knowledge profile.

Composes oprim cognitive elements (compute_peer_percentile) to produce
a KC mastery map with peer comparison summary.

Pillars: fingerprint + decision_trail + report
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, CostTracker, Trail, build_result, compute_fingerprint


class KnowledgeProfilingConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "knowledge_profiling_workflow"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "decision_trail", "report"}
    _fingerprint_fields: ClassVar[set[str]] = {"user_id", "kc_list_hash"}

    peer_group_size: int = 100
    min_attempts_for_mastery: int = 3
    mastery_threshold: float = 0.8


class KnowledgeProfilingInput(BaseModel):
    user_id: str
    attempt_history: list[dict] = []
    peer_mastery_map: dict[str, float] = {}
    kc_labels: dict[str, str] = {}


def _compute_mastery_map(
    attempt_history: list[dict], min_attempts: int
) -> dict[str, float]:
    by_kc: dict[str, list[bool]] = {}
    for rec in attempt_history:
        kc = rec.get("kc_id", "")
        correct = bool(rec.get("correct", False))
        by_kc.setdefault(kc, []).append(correct)

    return {
        kc: sum(results) / len(results)
        for kc, results in by_kc.items()
        if len(results) >= min_attempts
    }


async def knowledge_profiling_workflow(
    config: KnowledgeProfilingConfig,
    input_data: KnowledgeProfilingInput,
    output_dir: Path,
    *,
    caller: Any = None,
    on_step: Any = None,
) -> dict:
    cost = CostTracker()
    trail = Trail()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        trail.record(event="start", user_id=input_data.user_id)

        mastery_map = _compute_mastery_map(
            input_data.attempt_history, config.min_attempts_for_mastery
        )
        trail.record(event="mastery_computed", kc_count=len(mastery_map))

        peer_percentile: dict[str, float] = {}
        if input_data.peer_mastery_map:
            try:
                from oprim.compute_peer_percentile import compute_peer_percentile
                for kc, student_val in mastery_map.items():
                    peer_val = input_data.peer_mastery_map.get(kc, student_val)
                    peer_percentile[kc] = compute_peer_percentile(
                        student_val, peer_val, config.peer_group_size
                    )
            except Exception:
                pass

        weak = [kc for kc, m in mastery_map.items() if m < config.mastery_threshold]
        strong = [kc for kc, m in mastery_map.items() if m >= config.mastery_threshold]
        overall = sum(mastery_map.values()) / max(len(mastery_map), 1)

        kc_list_hash = str(sorted(mastery_map.keys()))[:32]
        fp = compute_fingerprint({"user_id": input_data.user_id, "kc_list_hash": kc_list_hash})

        report = (
            f"# Knowledge Profile — {input_data.user_id}\n\n"
            f"Overall mastery: {overall:.1%}\n"
            f"Strong KCs ({len(strong)}): {', '.join(strong) or 'none'}\n"
            f"Weak KCs ({len(weak)}): {', '.join(weak) or 'none'}\n"
        )
        report_path = output_dir / "knowledge_profile.md"
        report_path.write_text(report, encoding="utf-8")

        trail_path = trail.write(output_dir)
        trail.record(event="done")

        if on_step:
            on_step("knowledge_profiling_workflow", "done")

        return build_result(
            status="ok",
            fingerprint=fp,
            trail=trail,
            trail_path=trail_path,
            report_path=report_path,
            cost_usd=cost.total_usd,
            mastery_map=mastery_map,
            weak_kcs=weak,
            strong_kcs=strong,
            peer_percentile=peer_percentile,
            overall_mastery=overall,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="error",
            error={"type": type(exc).__name__, "message": str(exc)},
            cost_usd=cost.total_usd,
        )
