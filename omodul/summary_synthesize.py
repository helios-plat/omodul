"""
omodul.summary_synthesize — Community KU synthesis workflow.

Pillars: decision_trail
Fingerprint fields: community_label

Synthesizes a set of KUs from one community into a single coherent
summary KU. The synthesis grade cannot exceed the highest source KU grade,
and is capped at "high".

Mandates (CI-checked):
  - is_synthesis=True hardcoded
  - synthesis_note="AII综合，非原文断言" hardcoded
  - grade = min(max_source_grade, "high")
"""
from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, field_validator

from obase.provider_registry import ProviderRegistry

from omodul._base import (
    BaseConfig, CostTracker, Trail, build_result, compute_fingerprint,
)


# ---------------------------------------------------------------------------
# Grade helpers
# ---------------------------------------------------------------------------

_GRADE_RANKS: dict[str, int] = {
    "unverified": 0, "low": 1, "medium": 2, "high": 3, "verified": 4, "proven": 5,
}
_GRADE_BY_RANK = {v: k for k, v in _GRADE_RANKS.items()}


def _max_grade(grades: list[str]) -> str:
    if not grades:
        return "unverified"
    return max(grades, key=lambda g: _GRADE_RANKS.get(g, 0))


def _cap_grade(grade: str, cap: str) -> str:
    cap_rank = _GRADE_RANKS.get(cap, 3)
    grade_rank = _GRADE_RANKS.get(grade, 0)
    return cap if grade_rank > cap_rank else grade


# ---------------------------------------------------------------------------
# Config / Findings
# ---------------------------------------------------------------------------

class SummarySynthesizeConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "summary_synthesize"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set] = {"decision_trail"}
    _fingerprint_fields: ClassVar[set] = {"community_label"}

    community_label: str
    max_source_kus: int = 20


class SummarySynthesizeFindings(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    summary_ku_id: str
    summary_text: str
    is_synthesis: bool = True
    synthesis_note: str = "AII综合，非原文断言"
    grade: str
    source_ku_ids: list[str]

    @field_validator("is_synthesis", mode="before")
    @classmethod
    def _force_is_synthesis(cls, v: Any) -> bool:
        return True

    @field_validator("synthesis_note", mode="before")
    @classmethod
    def _force_synthesis_note(cls, v: Any) -> str:
        return "AII综合，非原文断言"


# ---------------------------------------------------------------------------
# Fingerprint helper
# ---------------------------------------------------------------------------

def compute_fingerprint_for_summary_synthesize(community_label: str) -> str:
    return compute_fingerprint({"community_label": community_label})


# ---------------------------------------------------------------------------
# Workflow
# ---------------------------------------------------------------------------

async def summary_synthesize(
    config: SummarySynthesizeConfig,
    input_data: Any,   # SummarySynthesizeInput (oprim._aii_graph_types)
    output_dir: Path,
    *,
    on_step=None,
) -> dict:
    """Synthesize a community of KUs into one summary KU.

    Returns build_result dict with decision_trail, findings, and grade.
    """
    trail = Trail()
    cost = CostTracker()
    fingerprint = compute_fingerprint_for_summary_synthesize(config.community_label)

    ku_ids = list(getattr(input_data, "ku_ids", []))
    if not ku_ids:
        return build_result(
            status="failed",
            error={"type": "ValueError", "message": "ku_ids is empty"},
            fingerprint=fingerprint,
            trail=trail,
            trail_path=None,
            cost_usd=0.0,
        )

    try:
        llm = ProviderRegistry.get().llm(config.llm_provider)

        ku_texts: list[str] = list(getattr(input_data, "ku_texts", []))
        source_grades: list[str] = list(getattr(input_data, "source_grades", []))

        # Truncate to max_source_kus
        ku_ids = ku_ids[: config.max_source_kus]
        ku_texts = ku_texts[: config.max_source_kus]
        source_grades = source_grades[: config.max_source_kus]

        trail.record(
            event="start",
            community_label=config.community_label,
            n_sources=len(ku_ids),
            fingerprint=fingerprint,
        )
        _notify(on_step, "synthesize", "started")

        # Compute synthesis grade: ≤ max source grade, capped at "high"
        synthesis_grade = _cap_grade(_max_grade(source_grades), "high")

        # Build KU block for LLM
        ku_block = "\n\n".join(
            f"[{i + 1}] {ku_ids[i]}\n{ku_texts[i] if i < len(ku_texts) else ''}"
            for i in range(len(ku_ids))
        )
        prompt = (
            f"综合以下来自同一社区的知识单元，生成一个连贯的综合摘要。\n\n"
            f"社区标签：{config.community_label}\n\n"
            f"知识单元：\n{ku_block}\n\n"
            f"请输出综合摘要文本（200-500字）："
        )

        resp = await llm(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2048,
        )
        summary_text = _extract_text(resp)
        cost.add_from_response(resp, model=config.llm_model)

        trail.record(event="synthesize_done", text_len=len(summary_text))
        _notify(on_step, "synthesize", "done")

        summary_ku_id = f"synthesis_{fingerprint[:8]}_{uuid.uuid4().hex[:6]}"

        findings = SummarySynthesizeFindings(
            summary_ku_id=summary_ku_id,
            summary_text=summary_text,
            grade=synthesis_grade,
            source_ku_ids=ku_ids,
        )

        trail_path = trail.write(output_dir)

        return build_result(
            status="completed",
            error=None,
            fingerprint=fingerprint,
            trail=trail,
            trail_path=trail_path,
            cost_usd=cost.total_usd,
            **findings.model_dump(),
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
            fingerprint=fingerprint,
            trail=trail,
            trail_path=None,
            cost_usd=cost.total_usd,
        )


def _extract_text(resp: dict) -> str:
    for block in resp.get("content", []):
        if isinstance(block, dict) and block.get("type") == "text":
            return block["text"].strip()
    return ""


def _notify(on_step: Any, step: str, state: str) -> None:
    if on_step is not None:
        try:
            on_step(step=step, state=state)
        except Exception:
            pass
