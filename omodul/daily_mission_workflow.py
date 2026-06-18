"""omodul.daily_mission_workflow — Generate daily practice missions.

Pure algorithm, no LLM, synchronous function.
Selects practice items using spaced-repetition priority + mastery gap weighting.

Pillars: fingerprint + decision_trail
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, CostTracker, Trail, build_result, compute_fingerprint


class DailyMissionConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "daily_mission_workflow"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "decision_trail"}
    _fingerprint_fields: ClassVar[set[str]] = {"user_id", "mission_date"}

    mission_count: int = 5
    review_ratio: float = 0.4
    mastery_gap_weight: float = 0.6
    difficulty_weight: float = 0.4


class MissionItem(BaseModel):
    question_id: str
    kc_id: str
    difficulty: float = 0.5
    mastery: float = 0.5
    days_since_last: int = 0


class DailyMissionInput(BaseModel):
    user_id: str
    mission_date: str = ""
    available_questions: list[dict] = []
    kc_mastery: dict[str, float] = {}
    last_seen_dates: dict[str, int] = {}


def _mission_priority(item: MissionItem) -> float:
    mastery_gap = 1.0 - item.mastery
    review_bonus = min(item.days_since_last / 7.0, 1.0) * 0.3
    return mastery_gap * 0.6 + item.difficulty * 0.4 + review_bonus


def daily_mission_workflow(
    config: DailyMissionConfig,
    input_data: DailyMissionInput,
    output_dir: Path,
    *,
    on_step: Any = None,
) -> dict:
    """纯算法，无LLM，同步函数."""
    cost = CostTracker()
    trail = Trail()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        trail.record(event="start", user_id=input_data.user_id, date=input_data.mission_date)

        items = [
            MissionItem(
                question_id=q.get("question_id", f"q{i}"),
                kc_id=q.get("kc_id", "unknown"),
                difficulty=float(q.get("difficulty", 0.5)),
                mastery=input_data.kc_mastery.get(
                    q.get("kc_id", ""), float(q.get("mastery", 0.5))
                ),
                days_since_last=input_data.last_seen_dates.get(q.get("question_id", ""), 99),
            )
            for i, q in enumerate(input_data.available_questions)
        ]

        review_target = int(config.mission_count * config.review_ratio)
        review_pool = [it for it in items if 0 < it.days_since_last <= 7]
        new_pool = [it for it in items if it not in review_pool]

        review_pool.sort(key=_mission_priority, reverse=True)
        new_pool.sort(key=_mission_priority, reverse=True)

        selected = review_pool[:review_target] + new_pool[:config.mission_count - review_target]
        if len(selected) < config.mission_count:
            remaining = [it for it in items if it not in selected]
            remaining.sort(key=_mission_priority, reverse=True)
            selected += remaining[:config.mission_count - len(selected)]

        selected = selected[:config.mission_count]
        trail.record(event="missions_selected", count=len(selected))

        fp = compute_fingerprint({
            "user_id": input_data.user_id,
            "mission_date": input_data.mission_date,
        })

        missions = [
            {
                "question_id": it.question_id,
                "kc_id": it.kc_id,
                "difficulty": it.difficulty,
                "priority": round(_mission_priority(it), 4),
            }
            for it in selected
        ]

        # 新增 subjects 分组模式 (Contract Change v2)
        subjects_map = {}
        for q in input_data.available_questions:
            # 假设输入中带有 subject 字段，若无则默认为 math
            subj = q.get("subject", "math")
            if subj not in subjects_map:
                subjects_map[subj] = []
            subjects_map[subj].append(q)

        # 简单按科目各取几个
        subjects_missions = {}
        for subj, q_list in subjects_map.items():
            subj_items = [
                MissionItem(
                    question_id=q.get("question_id", "q"),
                    kc_id=q.get("kc_id", "unknown"),
                    difficulty=float(q.get("difficulty", 0.5)),
                    mastery=input_data.kc_mastery.get(q.get("kc_id", ""), 0.5),
                    days_since_last=input_data.last_seen_dates.get(q.get("question_id", ""), 99)
                )
                for q in q_list
            ]
            subj_items.sort(key=_mission_priority, reverse=True)
            subjects_missions[subj] = [
                {
                    "question_id": it.question_id,
                    "kc_id": it.kc_id,
                    "difficulty": it.difficulty
                }
                for it in subj_items[:2] # 每科取2个作为演示
            ]

        trail_path = trail.write(output_dir)
        trail.record(event="done")

        if on_step:
            on_step("daily_mission_workflow", "done")

        return {
            "status": "ok",
            "fingerprint": fp,
            "decision_trail": trail.steps,
            "trail_path": str(trail_path),
            "cost_usd": cost.total_usd,
            "missions": missions, # 旧契约保留
            "subjects": subjects_missions # 新增多科目分组契约
        }

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="error",
            error={"type": type(exc).__name__, "message": str(exc)},
            cost_usd=0.0,
        )
