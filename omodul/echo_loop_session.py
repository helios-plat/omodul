"""
Echo-Loop 学习闭环 业务事务（omodul 层）
===========================================

标准签名: (config, input, output_dir) -> dict
支柱: decision_trail, cost, fingerprint

组合:
- oskill.run_complete_echo_loop (学习循环编排)
- oprim.cognitive_update (认知更新，已在 oskill 内部调用)
- oprim.spaced_review_schedule (间隔复习调度)

持久化: services/echo_loop_service.py (Layer 4)
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar, Optional

from pydantic import BaseModel, ConfigDict, Field

from omodul._base import BaseConfig, CostTracker, Trail, build_result
from oskill.echo_loop_skill import EchoLoopResult, run_complete_echo_loop
from obase.cognitive_types import KCState


# ──────────────────────────────────────────────────────────────────────────────
# 配置
# ──────────────────────────────────────────────────────────────────────────────

class EchoLoopConfig(BaseConfig):
    """Echo-Loop 事务配置。"""
    _omodul_name: ClassVar[str] = "echo_loop_session"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail", "cost", "fingerprint"}

    # 各阶段启用开关
    enable_blind_listen: bool = True
    enable_intensive_listen: bool = True
    enable_shadowing: bool = True
    enable_retell: bool = True

    # 阈值
    passing_threshold: float = 0.6
    min_review_interval_hours: float = 1.0  # 浓缩练习去抖阈值

    # LLM 配置
    llm_model: str = "claude-sonnet-4-6"


class EchoLoopInput(BaseModel):
    """事务输入。"""
    subject_ref: str
    audio_b64: str
    transcript: str
    student_retell: str = ""
    student_shadowing_audio_b64: Optional[str] = None
    reference_kc_ids: list[str] = Field(default_factory=list)
    # 已有的认知状态（从 DB 加载）
    kc_state: KCState
    card_dict: dict = Field(default_factory=dict)

    model_config = ConfigDict(arbitrary_types_allowed=True)


# ──────────────────────────────────────────────────────────────────────────────
# 主事务函数
# ──────────────────────────────────────────────────────────────────────────────

async def echo_loop_session(
    config: EchoLoopConfig,
    input_data: EchoLoopInput,
    output_dir: Path,
    *,
    on_step=None,
) -> dict:
    """
    运行完整的 Echo-Loop 学习会话。

    标准 omodul 签名：(config, input, output_dir) -> dict

    返回: build_result dict，包含 decision_trail, cost_usd, 结果数据
    """
    trail = Trail()
    cost = CostTracker()
    session_id = uuid.uuid4().hex[:16]

    try:
        trail.record(
            event="session_start",
            session_id=session_id,
            subject_ref=input_data.subject_ref,
            kc_ids=input_data.reference_kc_ids,
            transcript_length=len(input_data.transcript),
        )

        # 运行完整学习循环 (oskill)
        result: EchoLoopResult = await run_complete_echo_loop(
            audio_b64=input_data.audio_b64,
            transcript=input_data.transcript,
            student_retell=input_data.student_retell,
            reference_kc_ids=input_data.reference_kc_ids,
            kc_state=input_data.kc_state,
            card_dict=input_data.card_dict,
            student_audio_b64=input_data.student_shadowing_audio_b64,
        )

        trail.record(
            event="session_complete",
            session_id=session_id,
            overall_progress=result.effective_mastery,
            retell_passing=result.retell.is_passing if result.retell else False,
            shadowing_passing=result.shadowing.is_passing if result.shadowing else None,
        )

        # 持久化轨迹
        trail_path = trail.write(output_dir)

        # 返回标准 build_result
        return build_result(
            status="completed",
            error=None,
            trail=trail,
            trail_path=trail_path,
            cost_usd=cost.total_usd,
            session_id=session_id,
            effective_mastery=result.effective_mastery,
            kc_state=result.kc_state,
            card_dict=result.card_dict,
            blind_listen=result.blind_listen.model_dump() if result.blind_listen else None,
            intensive_listen=result.intensive_listen.model_dump() if result.intensive_listen else None,
            shadowing=result.shadowing.model_dump() if result.shadowing else None,
            retell=result.retell.model_dump() if result.retell else None,
        )

    except asyncio.CancelledError:
        trail.record(event="cancelled")
        trail.write(output_dir)
        raise

    except Exception as exc:
        trail.record(event="error", error_type=type(exc).__name__, message=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
            trail=trail,
            trail_path=None,
            cost_usd=cost.total_usd,
        )


# ──────────────────────────────────────────────────────────────────────────────
# 间隔复习调度 omodul (独立复用)
# ──────────────────────────────────────────────────────────────────────────────

class SpacedReviewConfig(BaseConfig):
    """间隔复习调度配置。"""
    _omodul_name: ClassVar[str] = "spaced_review_recommendation"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail", "fingerprint"}

    # Echo-Loop 间隔表（小时）：6h, 1d, 2d, 4d, 7d, 14d, 28d
    review_intervals_hours: list[int] = Field(
        default_factory=lambda: [6, 24, 48, 96, 168, 336, 672]
    )
    max_reviews: int = 7


class SpacedReviewInput(BaseModel):
    """间隔复习调度输入。"""
    subject_ref: str
    material_id: str
    current_stage: int = 0  # 0=首学, 1-7=复习轮次
    card_dict: dict = Field(default_factory=dict)
    now: Optional[datetime] = None


async def spaced_review_schedule(
    config: SpacedReviewConfig,
    input_data: SpacedReviewInput,
    output_dir: Path,
    *,
    on_step=None,
) -> dict:
    """
    生成非权威的复习建议。最终 due/FSRS state 只能由 oprim FSRS adapter 产生。

    固定间隔表仅作为 UI scaffold，不写 next_review_at，也不推进 review state。
    """
    trail = Trail()
    _now = input_data.now or datetime.now(timezone.utc)

    try:
        trail.record(
            event="schedule_calc",
            subject_ref=input_data.subject_ref,
            material_id=input_data.material_id,
            current_stage=input_data.current_stage,
        )

        next_stage = input_data.current_stage + 1
        next_review_hours = None
        is_completed = False

        if next_stage <= config.max_reviews:
            next_review_hours = config.review_intervals_hours[next_stage - 1]
        else:
            is_completed = True

        # Recommendation only: FSRS remains the sole scheduling authority.
        next_review_at = None

        trail.record(
            event="schedule_complete",
            next_stage=next_stage,
            next_review_hours=next_review_hours,
            next_review_at=None,
            recommendation_only=True,
            is_completed=is_completed,
        )

        trail_path = trail.write(output_dir)

        return build_result(
            status="completed",
            error=None,
            trail=trail,
            trail_path=trail_path,
            cost_usd=0.0,
            next_stage=next_stage,
            next_review_at=None,
            recommendation_only=True,
            is_completed=is_completed,
        )

    except Exception as exc:
        trail.record(event="error", error_type=type(exc).__name__, message=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
            trail=trail,
            trail_path=None,
            cost_usd=0.0,
        )


# ──────────────────────────────────────────────────────────────────────────────
# 难句归档 omodul
# ──────────────────────────────────────────────────────────────────────────────

class DifficultSentenceConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "difficult_sentence_archive"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail", "cost"}


class DifficultSentenceInput(BaseModel):
    subject_ref: str
    material_id: str
    sentence: str
    sentence_index: int
    difficulty: float
    reason: str
    transcript: str
    kc_ids: list[str] = Field(default_factory=list)


async def difficult_sentence_archive(
    config: DifficultSentenceConfig,
    input_data: DifficultSentenceInput,
    output_dir: Path,
    *,
    on_step=None,
) -> dict:
    """将难句归档到数据库，用于后续专项复习。"""
    trail = Trail()
    cost = CostTracker()

    try:
        trail.record(
            event="archive_difficult_sentence",
            subject_ref=input_data.subject_ref,
            material_id=input_data.material_id,
            sentence_index=input_data.sentence_index,
            difficulty=input_data.difficulty,
            reason=input_data.reason,
        )

        trail_path = trail.write(output_dir)

        return build_result(
            status="completed",
            error=None,
            trail=trail,
            trail_path=trail_path,
            cost_usd=cost.total_usd,
            archived=True,
            sentence_id=f"diff_{uuid.uuid4().hex[:12]}",
        )

    except Exception as exc:
        trail.record(event="error", error_type=type(exc).__name__, message=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
            trail=trail,
            trail_path=None,
            cost_usd=cost.total_usd,
        )


# ──────────────────────────────────────────────────────────────────────────────
# 语境化闪卡生成 omodul
# ──────────────────────────────────────────────────────────────────────────────

class ContextualFlashcardConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "contextual_flashcard_generate"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail", "cost"}


class ContextualFlashcardInput(BaseModel):
    subject_ref: str
    material_id: str
    difficult_sentences: list[dict]  # 来自 intensive_listen
    vocabulary: list[str] = Field(default_factory=list)  # 来自 retell/other


async def contextual_flashcard_generate(
    config: ContextualFlashcardConfig,
    input_data: ContextualFlashcardInput,
    output_dir: Path,
    *,
    on_step=None,
) -> dict:
    """
    生成语境化闪卡：结合原句上下文复习单词/短语。

    Echo-Loop 核心特性：不孤立背词，而是在原句语境中记忆。
    """
    trail = Trail()
    cost = CostTracker()

    try:
        trail.record(
            event="generate_flashcards",
            subject_ref=input_data.subject_ref,
            material_id=input_data.material_id,
            difficult_count=len(input_data.difficult_sentences),
            vocab_count=len(input_data.vocabulary),
        )

        # 生成闪卡：每张卡包含
        # - 目标词/短语
        # - 原句上下文（前后句）
        # - 翻译/释义
        flashcards = []
        for sent in input_data.difficult_sentences:
            flashcards.append({
                "type": "sentence",
                "text": sent.get("text", ""),
                "context_before": "",
                "context_after": "",
                "difficulty": sent.get("difficulty", 0.5),
                "kc_ids": input_data.kc_ids if hasattr(input_data, "kc_ids") else [],
            })

        for vocab in input_data.vocabulary:
            flashcards.append({
                "type": "vocabulary",
                "text": vocab,
                "context_before": "",
                "context_after": "",
                "difficulty": 0.5,
                "kc_ids": [],
            })

        trail.record(event="flashcards_generated", count=len(flashcards))

        trail_path = trail.write(output_dir)

        return build_result(
            status="completed",
            error=None,
            trail=trail,
            trail_path=trail_path,
            cost_usd=cost.total_usd,
            flashcards=flashcards,
        )

    except Exception as exc:
        trail.record(event="error", error_type=type(exc).__name__, message=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
            trail=trail,
            trail_path=None,
            cost_usd=cost.total_usd,
        )
