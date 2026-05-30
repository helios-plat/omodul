"""omodul.macro_daily_report — 宏观日报生成.

Pillars: fingerprint + decision_trail + report + cost
H1 compliant: calls oskills only (no sibling omodul).
Composition (all B10 oskills):
  - oskill.macro_surprise_compute  (async → ThreadPoolExecutor)
  - oskill.macro_cycle_engine_v2   (async → ThreadPoolExecutor)
  - oskill.policy_sector_attribution (async → ThreadPoolExecutor)
  - LLM narrative via ProviderRegistry
"""

from __future__ import annotations

import asyncio
import json
import traceback
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, ClassVar, Literal, Set

from obase.cost_tracker import CostTracker
from pydantic import BaseModel, Field

from omodul._base_config import BaseConfig
from omodul._decision_trail import build_decision_trail, record_step
from omodul._fingerprint import compute_fingerprint
from omodul._report import write_markdown_report


class MacroDailyReportConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "macro_daily_report"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[Set[str]] = {"fingerprint", "decision_trail", "report", "cost"}
    _fingerprint_fields: ClassVar[Set[str]] = {"trade_date", "report_type"}

    trade_date: date
    report_type: Literal["daily", "weekly"] = "daily"
    lookback_months: int = 6
    surprise_top_n: int = 5
    policy_top_n: int = 8


class MacroDailyReportInput(BaseModel):
    policy_news: list[dict[str, Any]] = Field(
        default_factory=list,
        description="PolicyNews-compatible dicts (content: str, source: str, published_at: str)",
    )
    industry_keyword_map: dict[str, str] = Field(
        default_factory=dict,
        description="概念关键词 → 申万行业 映射",
    )
    source: Literal["akshare"] = "akshare"


class MacroDailyReportFindings(BaseModel):
    trade_date: str
    report_type: str
    cycle_phase: str = "uncertain"
    cycle_confidence: float = 0.0
    surprise_shock_count: int = 0
    surprise_top_surprises: list[dict[str, Any]] = Field(default_factory=list)
    policy_attributed_count: int = 0
    policy_top_sectors: list[dict[str, Any]] = Field(default_factory=list)
    narrative: str = ""


def compute_fingerprint_for(
    config: MacroDailyReportConfig,
    input_data: MacroDailyReportInput,
) -> str:
    return compute_fingerprint(config, input_data)


def macro_daily_report(
    config: MacroDailyReportConfig,
    input_data: MacroDailyReportInput,
    output_dir: Path | None = None,
    *,
    on_step: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Generate macro daily report from surprise + cycle + policy data.

    Returns dict with: findings, fingerprint, decision_trail, report_path,
    cost_usd, status, error.
    """
    from oskill.macro_surprise_compute import macro_surprise_compute
    from oskill.macro_cycle_engine_v2 import macro_cycle_engine_v2
    from oskill.policy_sector_attribution import policy_sector_attribution
    from oprim.policy_event_extraction import PolicyNews

    started_at = datetime.now(UTC)
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
    cost_tracker = CostTracker()
    trail_steps: list[dict[str, Any]] = []
    fingerprint = compute_fingerprint_for(config, input_data)

    findings: MacroDailyReportFindings | None = None
    error: dict[str, Any] | None = None
    status: Literal["completed", "failed"] = "completed"
    trail: dict[str, Any] = {}

    try:
        # Step 1: concurrent async oskill calls via ThreadPoolExecutor
        t0 = datetime.now(UTC)

        def _run_surprise() -> Any:
            return asyncio.run(macro_surprise_compute(source=input_data.source))

        def _run_cycle() -> Any:
            return asyncio.run(
                macro_cycle_engine_v2(
                    lookback_months=config.lookback_months,
                    source=input_data.source,
                )
            )

        with ThreadPoolExecutor(max_workers=2) as exe:
            fut_surprise = exe.submit(_run_surprise)
            fut_cycle = exe.submit(_run_cycle)
            surprise_report = fut_surprise.result()
            cycle_result = fut_cycle.result()

        record_step(
            trail_steps=trail_steps,
            on_step=on_step,
            layer="oskill",
            callable_name="macro_surprise_compute+macro_cycle_engine_v2",
            inputs_summary={"source": input_data.source, "lookback_months": config.lookback_months},
            outputs_summary={
                "shock_count": surprise_report.shock_count,
                "cycle_phase": cycle_result.phase,
            },
            started_at=t0,
        )

        # Step 2: policy attribution (needs PolicyNews objects)
        t0 = datetime.now(UTC)
        news_objs: list[Any] = []
        for n in input_data.policy_news:
            try:
                news_objs.append(PolicyNews(**n))
            except Exception:
                pass

        policy_result = asyncio.run(
            policy_sector_attribution(
                news=news_objs,
                industry_keyword_map=input_data.industry_keyword_map,
                as_of_date=config.trade_date,
                top_n=config.policy_top_n,
                source=input_data.source,
            )
        )

        record_step(
            trail_steps=trail_steps,
            on_step=on_step,
            layer="oskill",
            callable_name="policy_sector_attribution",
            inputs_summary={"news_count": len(news_objs)},
            outputs_summary={"attributed_count": policy_result.attributed_count},
            started_at=t0,
        )

        # Step 3: LLM narrative
        t0 = datetime.now(UTC)
        narrative = ""
        try:
            from obase import ProviderRegistry

            llm = ProviderRegistry.get(category="llm", name=config.llm_provider)
            prompt = (
                f"日期: {config.trade_date}\n"
                f"宏观周期: {cycle_result.phase} (置信度 {cycle_result.confidence:.0%})\n"
                f"数据超预期冲击: {surprise_report.shock_count} 项\n"
                f"政策映射板块: {policy_result.attributed_count} 条\n\n"
                "请用 3-5 句话生成中文宏观日报摘要，要求简洁专业。"
            )
            resp = llm.call(prompt)  # type: ignore[attr-defined]
            narrative = resp if isinstance(resp, str) else str(resp)
        except Exception:
            narrative = f"[cycle={cycle_result.phase}; shocks={surprise_report.shock_count}]"

        record_step(
            trail_steps=trail_steps,
            on_step=on_step,
            layer="external",
            callable_name="llm_narrative",
            inputs_summary={"model": config.llm_model},
            outputs_summary={"narrative_len": len(narrative)},
            started_at=t0,
        )

        top_surprises = [s.model_dump() for s in surprise_report.surprises[: config.surprise_top_n]]
        top_sectors = (
            [
                {"sector_name": r.sector_name, "actual_change_pct": r.actual_change_pct}
                for r in policy_result.rows[:5]
            ]
            if hasattr(policy_result, "rows")
            else []
        )

        findings = MacroDailyReportFindings(
            trade_date=str(config.trade_date),
            report_type=config.report_type,
            cycle_phase=cycle_result.phase,
            cycle_confidence=cycle_result.confidence,
            surprise_shock_count=surprise_report.shock_count,
            surprise_top_surprises=top_surprises,
            policy_attributed_count=policy_result.attributed_count,
            policy_top_sectors=top_sectors,
            narrative=narrative,
        )

    except Exception as exc:
        status = "failed"
        error = {
            "type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
        }

    finally:
        trail = build_decision_trail(
            fingerprint=fingerprint,
            config=config,
            input_data=input_data,
            trail_steps=trail_steps,
            cost_tracker=cost_tracker,
            started_at=started_at,
            status=status,
            error=error,
        )
        if output_dir:
            (output_dir / "decision_trail.json").write_text(
                json.dumps(trail, indent=2, default=str), encoding="utf-8"
            )

    result: dict[str, Any] = {
        "findings": findings.model_dump() if findings else None,
        "fingerprint": fingerprint,
        "decision_trail": trail,
        "status": status,
        "error": error,
        "cost_usd": cost_tracker.total_usd,
    }

    if output_dir and findings is not None:
        try:
            rp = write_markdown_report(
                output_dir=output_dir,
                omodul_name="macro_daily_report",
                fingerprint=fingerprint,
                config=config,
                findings=findings,
                decision_trail=trail,
                cost_tracker=cost_tracker,
                status=status,
            )
            result["report_path"] = rp
        except Exception:
            result["report_path"] = None
    else:
        result["report_path"] = None

    return result
