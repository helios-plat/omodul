"""omodul.daily_digest_workflow — 每日学习摘要工作流。

Pillars: fingerprint + report
Composition:
  - oskill.hybrid_search (already in oskill library, async)
  - oprim.llm_summarize (Batch 1)
  - oskill.generate_derivative (already in oskill library, async)
"""

from __future__ import annotations

import asyncio
import traceback
from datetime import date
from pathlib import Path
from typing import Any, ClassVar, Literal

from obase.cost_tracker import CostTracker
from pydantic import BaseModel

from omodul._base_config import BaseConfig
from omodul._fingerprint import compute_fingerprint
from omodul._report import write_markdown_report


class DailyDigestConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "daily_digest_workflow"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "report"}
    _fingerprint_fields: ClassVar[set[str]] = {"digest_date", "user_id_hash"}

    digest_date: date
    user_id_hash: str
    corpus_id: str = "default"
    max_items: int = 20
    digest_style: str = "morning_brief"  # "morning_brief" | "evening_review"
    llm_provider: str = "qwen3"
    llm_model: str = "qwen3-max"


class DailyDigestInput(BaseModel):
    recent_substrate_ids: list[str] = []  # Substrate IDs to include (pre-fetched)
    custom_notes: list[str] = []  # Additional user-provided notes


class DailyDigestFindings(BaseModel):
    digest_text: str
    note_id: str | None = None  # Generated derivative note ID
    item_count: int
    digest_date: str


def daily_digest_workflow(
    config: DailyDigestConfig,
    input_data: DailyDigestInput,
    output_dir: Path,
) -> dict[str, Any]:
    """Generate a daily learning digest from recent substrates.

    Internal oskill composition (depth-1):
      - oskill.hybrid_search — retrieve today's content (async)
      - oskill.generate_derivative — create digest note (async)

    Internal oprim composition:
      - oprim.llm_summarize — synthesize digest text
    """
    enabled = config._enabled_pillars
    fingerprint = compute_fingerprint(config, input_data) if "fingerprint" in enabled else None
    cost_tracker = CostTracker(budget_usd=config.budget_usd) if "cost" in enabled else None
    error_info = None
    status: Literal["completed", "failed"] = "completed"
    findings: DailyDigestFindings | None = None

    try:
        # Stage 1: Fetch recent substrates via hybrid_search
        from oskill.hybrid_search import hybrid_search

        search_results = asyncio.run(
            hybrid_search(
                config.digest_date.strftime("%Y-%m-%d learning digest"),
                corpus_id=config.corpus_id,
                top_k=config.max_items,
                mode="augmented",
            )
        )

        # Combine search results with caller-provided substrate IDs and notes
        all_texts: list[str] = [
            r.highlight or r.title for r in search_results if r.highlight or r.title
        ]
        all_texts.extend(input_data.custom_notes)

        # Deduplicate and limit
        unique_texts = list(dict.fromkeys(all_texts))[: config.max_items]
        combined_text = "\n\n".join(unique_texts) if unique_texts else "(no content for today)"

        # Stage 2: Summarize via LLM
        from oprim import llm_summarize

        style_map = {
            "morning_brief": "bullet_points",
            "evening_review": "detailed",
        }
        summarize_style = style_map.get(config.digest_style, "concise")
        summarize_result = llm_summarize(
            text=combined_text,
            max_length=600,
            provider=config.llm_provider,
            model=config.llm_model,
            style=summarize_style,  # type: ignore[arg-type]
        )

        # Stage 3: Generate derivative note
        from oskill.knowledge.generate_derivative import generate_derivative

        note_id: str | None = None
        if input_data.recent_substrate_ids:
            raw_note_id = asyncio.run(
                generate_derivative(
                    input_data.recent_substrate_ids[0],
                    Path(input_data.recent_substrate_ids[0]),
                    "digest_note",
                )
            )
            note_id = str(raw_note_id) if raw_note_id else None

        findings = DailyDigestFindings(
            digest_text=summarize_result.summary,
            note_id=note_id,
            item_count=len(unique_texts),
            digest_date=config.digest_date.isoformat(),
        )

    except Exception as e:
        error_info = {
            "error_class": type(e).__name__,
            "error_message": str(e),
            "traceback": traceback.format_exc(),
        }
        status = "failed"

    report_path: Path | None = None
    if "report" in enabled and status == "completed" and findings:
        report_path = write_markdown_report(
            output_dir=output_dir,
            omodul_name=config._omodul_name,
            fingerprint=fingerprint or "",
            config=config,
            findings=findings,
            decision_trail={},
            cost_tracker=cost_tracker or CostTracker(budget_usd=0.0),
            status=status,
        )

    return {
        "findings": findings,
        "status": status,
        "error": error_info,
        "fingerprint": fingerprint,
        "decision_trail": None,
        "report_path": report_path,
        "cost_usd": cost_tracker.total_usd if cost_tracker else 0.0,
    }


def compute_fingerprint_for(config: DailyDigestConfig, input_data: DailyDigestInput) -> str:
    return compute_fingerprint(config, input_data)
