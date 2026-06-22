"""omodul.reading_guide_workflow — 阅读理解引导业务事务

标准签名: (config, input, output_dir, *, caller, on_step) -> dict
支柱: fingerprint + decision_trail + cost
subject 参数区分 english/chinese 两个引导语境。

Added: omodul v1.30.7
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, CostTracker, Trail, build_result, compute_fingerprint


class ReadingGuideConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "reading_guide_workflow"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "decision_trail", "cost"}
    _fingerprint_fields: ClassVar[set[str]] = {"question_hash", "user_id", "subject"}

    max_turns: int = 15
    model: str = "claude-sonnet-4-6"


class ReadingGuideInput(BaseModel):
    article_text: str
    question: str
    subject: str = "chinese"   # "english" or "chinese"
    student_messages: list[str] = []
    user_id: str = ""


async def reading_guide_workflow(
    config: ReadingGuideConfig,
    input_data: ReadingGuideInput,
    output_dir: Path,
    *,
    caller: Any = None,
    on_step: Any = None,
) -> dict:
    """阅读理解引导业务事务（english/chinese）。

    若 student_messages 为空 → 返回开场引导问（不含答案）。
    否则处理最新一条学生消息，返回下一个引导问题。

    Red line enforced at oskill layer: never reveals the direct answer.

    Returns
    -------
    dict
        status, fingerprint, trail_path, cost_usd,
        assistant_text, located_passage, answer_leaked, subject
    """
    from obase.provider_registry import ProviderRegistry
    from oskill import reading_comprehension_guide

    if caller is None:
        try:
            caller = ProviderRegistry.get().llm("default")
        except Exception:
            caller = _MockCaller(subject=input_data.subject)

    cost = CostTracker()
    trail = Trail()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        trail.record(event="start", user_id=input_data.user_id,
                     subject=input_data.subject,
                     n_messages=len(input_data.student_messages))

        messages = input_data.student_messages[: config.max_turns]

        result = await reading_comprehension_guide(
            article_text=input_data.article_text,
            question=input_data.question,
            subject=input_data.subject,
            student_messages=messages or None,
            caller=caller,
            model=config.model,
        )

        trail.record(
            event="guide_turn",
            located_passage=result.located_passage,
            answer_leaked=result.answer_leaked,
        )
        if on_step:
            on_step("reading_guide_workflow", f"reply::{result.assistant_text[:60]}")

        if result.answer_leaked:
            trail.record(event="redline_triggered")

        fp = compute_fingerprint({
            "question_hash": str(hash(input_data.question))[:12],
            "user_id":       input_data.user_id,
            "subject":       input_data.subject,
        })

        return build_result(
            status="ok",
            fingerprint=fp,
            trail=trail,
            trail_path=trail.write(output_dir),
            cost_usd=cost.total_usd,
            assistant_text=result.assistant_text,
            located_passage=result.located_passage,
            answer_leaked=result.answer_leaked,
            subject=input_data.subject,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        is_en = input_data.subject.lower() == "english"
        fallback = "Can you tell me what this question is asking?" if is_en \
                   else "这道题在考查什么？你先说说你的理解。"
        return build_result(
            status="error",
            error={"type": type(exc).__name__, "message": str(exc)},
            cost_usd=cost.total_usd,
            assistant_text=fallback,
            located_passage=False,
            answer_leaked=False,
            subject=input_data.subject,
        )


class _MockCaller:
    def __init__(self, subject: str = "chinese"):
        self.subject = subject

    async def __call__(self, **kwargs: Any) -> dict:
        text = '{"assistant_text":"Can you find the paragraph that discusses this?","located_passage":false,"answer_leaked":false}' \
               if self.subject.lower() == "english" else \
               '{"assistant_text":"你能找到原文中和这道题最相关的段落吗？","located_passage":false,"answer_leaked":false}'
        return {"content": text, "usage": {"input_tokens": 0, "output_tokens": 0}}
