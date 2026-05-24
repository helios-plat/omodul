from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field

from obase.cost_tracker import CostTracker
from obase.provider_registry import ProviderRegistry
from omodul._base_config import BaseConfig
from omodul._decision_trail import build_decision_trail, record_step
from omodul._fingerprint import compute_fingerprint


class ActivityItem(BaseModel):
    activity_id: str
    activity_type: str
    title: str
    timestamp_utc: str


class ActivityGroup(BaseModel):
    category: str
    count: int
    highlights: list[str] = Field(default_factory=list)


class WeeklyReviewConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "weekly_review_workflow"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {"time_window_days", "title_prefix"}

    time_window_days: int = 7
    title_prefix: str = "周回顾"


class WeeklyReviewInput(BaseModel):
    activities: list[ActivityItem]
    window_start_utc: datetime
    window_end_utc: datetime


class WeeklyReviewFindings(BaseModel):
    summary: str = ""
    groups: list[ActivityGroup] = Field(default_factory=list)


def compute_fingerprint_for(config: WeeklyReviewConfig, input_data: WeeklyReviewInput) -> str:
    return compute_fingerprint(config, input_data)


def _compute_fingerprint(config: WeeklyReviewConfig, input_data: WeeklyReviewInput) -> str:
    """内部指纹计算, 保持与 compute_fingerprint_for 一致."""
    return compute_fingerprint_for(config, input_data)


def weekly_review_workflow(
    config: WeeklyReviewConfig,
    input_data: WeeklyReviewInput,
    output_dir: Path,
    *,
    on_step: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """端到端周回顾工作流.

    主流程:
    1. _stage_llm_review: 使用 LLM 总结本周活动并分类
    2. _write_markdown_report: 生成 7 段式 Markdown 报告

    Example:
        ```python
        config = WeeklyReviewConfig(time_window_days=7)
        input_data = WeeklyReviewInput(
            activities=[ActivityItem(activity_id="1", activity_type="s", title="t", timestamp_utc="...")],
            window_start_utc=datetime.now(),
            window_end_utc=datetime.now()
        )
        res = weekly_review_workflow(config, input_data, Path("./out"))
        assert res["status"] == "completed"
        ```
    """
    started_at = datetime.now(UTC)
    cost_tracker = CostTracker()
    trail_steps: list[dict[str, Any]] = []
    status: Literal["completed", "failed", "partial"] = "completed"
    error: dict[str, Any] | None = None
    findings = WeeklyReviewFindings()

    fingerprint = _compute_fingerprint(config, input_data)
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "report.md"

    try:
        if not input_data.activities:
            with open(report_path, "w") as f:
                f.write(f"# {config.title_prefix}\n\nNo activities found.")
            return _build_response(
                findings, fingerprint, config, input_data, trail_steps,
                cost_tracker, started_at, status, error, output_dir, report_path
            )

        t0 = datetime.now(UTC)
        llm = ProviderRegistry.get_caller(provider=config.llm_provider, model=config.llm_model)

        prompt = f"""Please review the following activities and provide a summary.
Activities:
{json.dumps([a.model_dump() for a in input_data.activities], ensure_ascii=False)}

Provide a general summary and group them by category.
"""
        response = llm(messages=[{"role": "user", "content": prompt}], max_tokens=2000)
        llm_content = response.get("content", "No summary generated.")
        
        findings.summary = llm_content
        findings.groups = [
            ActivityGroup(category="General", count=len(input_data.activities))
        ]

        record_step(
            trail_steps=trail_steps,
            on_step=on_step,
            layer="external",
            callable_name="_stage_llm_review",
            inputs_summary={"activity_count": len(input_data.activities)},
            outputs_summary={"summary_len": len(llm_content)},
            started_at=t0,
        )

        _write_markdown_report(report_path, config, input_data, findings, fingerprint, cost_tracker)

    except Exception as e:
        status = "failed"
        error = {"type": type(e).__name__, "message": str(e)}
        _write_partial_report(report_path, config, e)
        _write_failed_marker(output_dir)

    return _build_response(
        findings, fingerprint, config, input_data, trail_steps,
        cost_tracker, started_at, status, error, output_dir, report_path
    )


def _build_response(
    findings: WeeklyReviewFindings,
    fingerprint: str,
    config: WeeklyReviewConfig,
    input_data: WeeklyReviewInput,
    trail_steps: list,
    cost_tracker: CostTracker,
    started_at: datetime,
    status: str,
    error: dict | None,
    output_dir: Path,
    report_path: Path,
) -> dict[str, Any]:
    decision_trail = build_decision_trail(
        fingerprint=fingerprint,
        config=config,
        input_data=input_data,
        trail_steps=trail_steps,
        cost_tracker=cost_tracker,
        started_at=started_at,
        status=status,
        error=error,
    )

    with open(output_dir / "decision_trail.json", "w") as f:
        json.dump(decision_trail, f, indent=2)

    return {
        "findings": findings.model_dump(),
        "fingerprint": fingerprint,
        "decision_trail": decision_trail,
        "report_path": str(report_path),
        "cost_usd": cost_tracker.total_usd,
        "status": status,
        "error": error,
    }


def _write_markdown_report(
    path: Path,
    config: WeeklyReviewConfig,
    input_data: WeeklyReviewInput,
    findings: WeeklyReviewFindings,
    fingerprint: str,
    cost_tracker: CostTracker,
) -> None:
    with open(path, "w") as f:
        f.write(f"# {config.title_prefix}\n\n")
        f.write("## Summary\n")
        f.write(f"{findings.summary}\n\n")
        f.write("## Config\n")
        f.write(f"Days: {config.time_window_days}\n\n")
        f.write("## Findings\n")
        f.write(f"Groups: {len(findings.groups)}\n\n")
        f.write("## Trail\n")
        f.write(f"Steps recorded.\n\n")
        f.write("## Cost\n")
        f.write(f"USD: {cost_tracker.total_usd}\n\n")
        f.write("## Reproducibility\n")
        f.write(f"Fingerprint: {fingerprint}\n")


def _write_partial_report(path: Path, config: WeeklyReviewConfig, error: Exception) -> None:
    partial_path = path.with_suffix(".partial.md")
    with open(partial_path, "w") as f:
        f.write(f"# {config.title_prefix} - FAILED\n\n")
        f.write(f"Error: {error}\n")


def _write_failed_marker(output_dir: Path) -> None:
    (output_dir / ".failed_marker").touch()
