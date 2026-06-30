"""
认知持久化业务事务
==================
omodul/cognitive.py
"""

from __future__ import annotations
from datetime import datetime, timezone
from typing import ClassVar, Optional, Any
from pathlib import Path
from uuid import UUID
from pydantic import BaseModel

from omodul.base import BaseConfig, build_fingerprint, standard_return
from oskill.cognitive_state import cognitive_update, CognitiveUpdateInput
from obase.cognitive_store import BaseCognitiveStore
from oprim.fsrs_engine import fsrs_due_date, fsrs_retrievability
from oprim.due_compute import due_compute

class InteractionConfig(BaseConfig):
    _omodul_name = "process_interaction_workflow"
    _omodul_version = "0.1.0"
    _fingerprint_fields = set()
    _enabled_pillars = {"decision_trail"}

class InteractionInput(BaseModel):
    student_id: UUID
    kc_id: str
    is_correct: bool
    question_type: str = "solve"
    question_id: Optional[UUID] = None
    source: str = "paper"
    used_answer: bool = False
    struggled: bool = False
    effortless: bool = False
    is_interleaved: bool = False
    time_spent_seconds: Optional[int] = None
    difficulty: Optional[float] = None   # 题目难度 b∈[0,1]（IRT）；None 时不改变行为
    predicted_confidence: Optional[float] = None  # JOL：作答前自评把握 ∈[0,1]（仅记录，不入算法）
    now: Optional[datetime] = None
    min_review_interval_hours: float = 0.0  # 集中练习去抖阈值，透传 cognitive_update；默认 0 不改变行为

class InteractionFindings(BaseModel):
    kc_id: str
    p_mastery: float
    long_term_mastery: float
    effective_mastery: float
    error_type: Optional[str]
    rating: str
    next_review_due: Optional[str]
    n_attempts: int

async def process_interaction_workflow(
    config: InteractionConfig,
    input_data: InteractionInput,
    store: BaseCognitiveStore,
    output_dir: Optional[Path] = None,
) -> dict:
    """处理一次认知交互并落库。

    DoD 1.3/1.4: 落 kc_mastery + 追加 interaction_events（只增不改），严守更新顺序红线。
    支持按照题型扩展。
    """
    now = input_data.now or datetime.now(timezone.utc)
    
    # 1. 获取当前状态 (传题型以获取正确的先验)
    state, card_dict = await store.get_or_create(input_data.student_id, input_data.kc_id, input_data.question_type)
    
    # 2. 调用认知更新算法 (oskill)
    update_input = CognitiveUpdateInput(
        state=state,
        card_dict=card_dict,
        is_correct=input_data.is_correct,
        used_answer=input_data.used_answer,
        struggled=input_data.struggled,
        effortless=input_data.effortless,
        is_interleaved=input_data.is_interleaved,
        difficulty=input_data.difficulty,
        min_review_interval_hours=input_data.min_review_interval_hours,
        now=now
    )
    result = cognitive_update(input=update_input)
    
    # 3. 落库：更新 kc_mastery
    await store.save(input_data.student_id, input_data.kc_id, result.state, result.card_dict)
    
    # 4. 落库：追加 interaction_events
    days_since_last = None
    if state.last_interaction_ts:
        days_since_last = (now - datetime.fromtimestamp(state.last_interaction_ts, timezone.utc)).total_seconds() / 86400.0
        
    event_data = {
        "question_id": input_data.question_id,
        "source": input_data.source,
        "is_correct": input_data.is_correct,
        "fsrs_rating": result.rating_val,
        "time_spent_seconds": input_data.time_spent_seconds,
        "days_since_last": days_since_last,
        "is_interleaved": input_data.is_interleaved,
        "item_difficulty": input_data.difficulty,
        "predicted_confidence": input_data.predicted_confidence,
        "occurred_at": now
    }
    await store.append_event(input_data.student_id, input_data.kc_id, event_data)
    
    # 5. 组装结果
    findings = InteractionFindings(
        kc_id=input_data.kc_id,
        p_mastery=round(result.state.current(), 4),
        long_term_mastery=round(result.state.long_term_mastery or result.state.current(), 4),
        effective_mastery=round(result.effective_mastery, 4),
        error_type=result.error_type,
        rating=result.rating,
        next_review_due=fsrs_due_date(card_dict=result.card_dict),
        n_attempts=result.state.n_attempts
    )
    
    trail = [
        {"step": "get_state", "kc_id": input_data.kc_id, "question_type": input_data.question_type},
        {"step": "cognitive_update", "result": findings.model_dump()},
        {"step": "save_state"},
        {"step": "append_event"}
    ]
    
    return standard_return(
        findings=findings,
        status="completed",
        trail=trail if "decision_trail" in config._enabled_pillars else None
    )

async def mastery_overview_workflow(store: BaseCognitiveStore, student_id: UUID, now: Optional[datetime] = None):
    """获取掌握度总览（业务逻辑）。"""
    now = now or datetime.now(timezone.utc)
    states_map = await store.get_all_states(student_id)
    out = []
    for kc_id, (state, card) in states_map.items():
        R = fsrs_retrievability(card_dict=card, now=now)
        long_term = state.long_term_mastery or state.current()
        out.append({
            "kc_id": kc_id,
            "long_term_mastery": round(long_term, 4),
            # 红线：effective = long_term × R（与 process 路径同口径，非 current()×R）
            "effective_mastery": round(long_term * R, 4),
            "n_attempts": state.n_attempts,
        })
    return sorted(out, key=lambda x: x["effective_mastery"])

async def review_queue_workflow(store: BaseCognitiveStore, student_id: UUID, now: Optional[datetime] = None):
    """今日到期复习池（业务逻辑）。"""
    now = now or datetime.now(timezone.utc)
    states_map = await store.get_all_states(student_id)
    queue = []
    for kc_id, (state, card) in states_map.items():
        # 单源到期判定（item 13）：统一用 due_compute，避免与其它路径语义分叉。
        if due_compute(card_dict=card, now=now):
            queue.append({"kc_id": kc_id, "due": fsrs_due_date(card_dict=card)})
    return queue
