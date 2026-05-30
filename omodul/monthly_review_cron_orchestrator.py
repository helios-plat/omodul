"""omodul.monthly_review_cron_orchestrator — 月度复盘 Cron 编排.

Pillars: fingerprint + decision_trail + report + cost
H1 compliant: calls oskills/oprims only (no sibling omodul).
Composition (B10 oskill + B8 oprim):
  - oskill.discipline_vs_violation_winrate_compute  (sync)
  - oprim.monthly_review_jinja2_render              (sync)
  - LLM narrative via ProviderRegistry
"""

from __future__ import annotations

import json
import traceback
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, Literal, Set

from obase.cost_tracker import CostTracker
from pydantic import BaseModel, Field

from omodul._base_config import BaseConfig
from omodul._decision_trail import build_decision_trail, record_step
from omodul._fingerprint import compute_fingerprint
from omodul._report import write_markdown_report


class MonthlyReviewConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "monthly_review_cron_orchestrator"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[Set[str]] = {"fingerprint", "decision_trail", "report", "cost"}
    _fingerprint_fields: ClassVar[Set[str]] = {"user_id_hash", "year_month"}

    user_id_hash: str
    year_month: str  # "2026-05"
    template_name: str = "monthly_review_default"


class MonthlyReviewInput(BaseModel):
    trade_records: list[dict[str, Any]] = Field(
        default_factory=list,
        description="TradeRecord-compatible dicts for the full month",
    )
    template_vars: dict[str, Any] = Field(
        default_factory=dict,
        description="Extra Jinja2 template variables (e.g. portfolio_value, benchmark_return)",
    )
    template_dir: str | None = Field(
        default=None,
        description="Path to Jinja2 template directory; None uses oprim built-in",
    )


class MonthlyReviewFindings(BaseModel):
    user_id_hash: str
    year_month: str
    discipline_win_rate: float = 0.0
    violation_win_rate: float = 0.0
    n_trades: int = 0
    n_compliant: int = 0
    n_violating: int = 0
    sharpe_ratio: float | None = None
    llm_narrative: str = ""
    rendered_report_text: str = ""


def compute_fingerprint_for(
    config: MonthlyReviewConfig,
    input_data: MonthlyReviewInput,
) -> str:
    return compute_fingerprint(config, input_data)


def monthly_review_cron_orchestrator(
    config: MonthlyReviewConfig,
    input_data: MonthlyReviewInput,
    output_dir: Path | None = None,
    *,
    on_step: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Orchestrate monthly review: winrate analysis + Jinja2 render + LLM narrative.

    Returns dict with: findings, fingerprint, decision_trail, report_path,
    cost_usd, status, error.
    """
    from oskill.discipline_vs_violation_winrate_compute import (
        TradeRecord,
        discipline_vs_violation_winrate_compute,
    )
    import oprim

    started_at = datetime.now(UTC)
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
    cost_tracker = CostTracker()
    trail_steps: list[dict[str, Any]] = []
    fingerprint = compute_fingerprint_for(config, input_data)

    findings: MonthlyReviewFindings | None = None
    error: dict[str, Any] | None = None
    status: Literal["completed", "failed"] = "completed"
    trail: dict[str, Any] = {}

    try:
        # Step 1: discipline winrate analysis
        t0 = datetime.now(UTC)
        records: list[TradeRecord] = []
        for r in input_data.trade_records:
            try:
                records.append(TradeRecord(**r))
            except Exception:
                pass

        disc_result = None
        if records:
            disc_result = discipline_vs_violation_winrate_compute(trade_records=records)

        n_compliant = disc_result.discipline.count if disc_result else 0
        n_violating = disc_result.violation.count if disc_result else 0
        sharpe = disc_result.discipline.sharpe if disc_result else None

        record_step(
            trail_steps=trail_steps,
            on_step=on_step,
            layer="oskill",
            callable_name="discipline_vs_violation_winrate_compute",
            inputs_summary={"n_records": len(records), "year_month": config.year_month},
            outputs_summary={
                "disc_win_rate": disc_result.discipline.win_rate if disc_result else 0,
                "viol_win_rate": disc_result.violation.win_rate if disc_result else 0,
            },
            started_at=t0,
        )

        # Step 2: Jinja2 render
        t0 = datetime.now(UTC)
        template_vars: dict[str, Any] = {
            "year_month": config.year_month,
            "user_id_hash": config.user_id_hash,
            "n_trades": len(records),
            "n_compliant": n_compliant,
            "n_violating": n_violating,
            "discipline_win_rate": disc_result.discipline.win_rate if disc_result else 0.0,
            "violation_win_rate": disc_result.violation.win_rate if disc_result else 0.0,
            **input_data.template_vars,
        }
        rendered_text = ""
        try:
            from pathlib import Path as _Path

            tmpl_dir: Path | str = (
                _Path(input_data.template_dir) if input_data.template_dir else "."
            )
            render_result = oprim.monthly_review_jinja2_render(
                template_name=config.template_name,
                context=template_vars,
                template_dir=tmpl_dir,
            )
            rendered_text = render_result.content
        except Exception:
            rendered_text = json.dumps(template_vars, ensure_ascii=False, indent=2)

        record_step(
            trail_steps=trail_steps,
            on_step=on_step,
            layer="oprim",
            callable_name="monthly_review_jinja2_render",
            inputs_summary={"template": config.template_name, "vars_count": len(template_vars)},
            outputs_summary={"rendered_len": len(rendered_text)},
            started_at=t0,
        )

        # Step 3: LLM narrative
        t0 = datetime.now(UTC)
        narrative = ""
        try:
            from obase import ProviderRegistry

            llm = ProviderRegistry.get(category="llm", name=config.llm_provider)
            disc_wr = disc_result.discipline.win_rate if disc_result else 0.0
            viol_wr = disc_result.violation.win_rate if disc_result else 0.0
            prompt = (
                f"月份: {config.year_month}\n"
                f"总交易: {len(records)} 笔 (守纪律 {n_compliant}, 违规 {n_violating})\n"
                f"纪律胜率: {disc_wr:.1%}, 违规胜率: {viol_wr:.1%}\n\n"
                "请用 4-6 句话生成月度复盘总结，指出纪律执行亮点与改进点，语言简洁专业。"
            )
            resp = llm.call(prompt)  # type: ignore[attr-defined]
            narrative = resp if isinstance(resp, str) else str(resp)
        except Exception:
            narrative = f"[{config.year_month}] 守纪律 {n_compliant} 笔 / 违规 {n_violating} 笔"

        record_step(
            trail_steps=trail_steps,
            on_step=on_step,
            layer="external",
            callable_name="llm_narrative",
            inputs_summary={"model": config.llm_model},
            outputs_summary={"narrative_len": len(narrative)},
            started_at=t0,
        )

        findings = MonthlyReviewFindings(
            user_id_hash=config.user_id_hash,
            year_month=config.year_month,
            discipline_win_rate=disc_result.discipline.win_rate if disc_result else 0.0,
            violation_win_rate=disc_result.violation.win_rate if disc_result else 0.0,
            n_trades=len(records),
            n_compliant=n_compliant,
            n_violating=n_violating,
            sharpe_ratio=sharpe,
            llm_narrative=narrative,
            rendered_report_text=rendered_text,
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
                omodul_name="monthly_review_cron_orchestrator",
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
