"""omodul.audience_data_workflow — Audience data collection + sentiment analysis pipeline.

Implements 4 pillars: fingerprint, decision_trail, report, cost.

Example:
    >>> from pathlib import Path
    >>> from omodul.audience_data_workflow import (
    ...     audience_data_workflow, AudienceDataConfig, AudienceDataInput,
    ... )
    >>> result = audience_data_workflow(
    ...     config=AudienceDataConfig(platform="youtube", video_ids=["abc"]),
    ...     input_data=AudienceDataInput(oauth_token="tok"),
    ...     output_dir=Path("output"),
    ... )

Raises:
    Various stage-specific errors captured in result["error"].
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, Literal

from obase.cost_tracker import CostTracker
from pydantic import BaseModel, Field

from omodul._base_config import BaseConfig
from omodul._decision_trail import build_decision_trail, record_step
from omodul._fingerprint import compute_fingerprint
from omodul._report import write_markdown_report

# --- Config / Input / Findings ---


class AudienceDataConfig(BaseConfig):
    """Configuration for audience data workflow."""

    _omodul_name: ClassVar[str] = "audience_data_workflow"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {"platform", "video_ids", "analysis_depth"}

    platform: Literal["youtube", "bilibili"]
    video_ids: list[str]
    analysis_depth: Literal["basic", "deep"] = "basic"
    max_comments_per_video: int = 100


class AudienceDataInput(BaseModel):
    """Per-execution inputs."""

    oauth_token: str | None = None
    cookies: dict[str, str] | None = None


class SentimentSummary(BaseModel):
    """Sentiment analysis summary."""

    positive_pct: float = 0.0
    negative_pct: float = 0.0
    neutral_pct: float = 0.0
    top_keywords: list[str] = Field(default_factory=list)


class FeedbackSummary(BaseModel):
    """Structured feedback summary."""

    positive_points: list[str] = Field(default_factory=list)
    negative_points: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)


class AudienceDataFindings(BaseModel):
    """Pipeline output findings."""

    videos_analyzed: int = 0
    total_views: int = 0
    total_comments: int = 0
    avg_completion_rate: float | None = None
    sentiment_summary: SentimentSummary = Field(default_factory=SentimentSummary)
    feedback: FeedbackSummary = Field(default_factory=FeedbackSummary)
    learnings: list[str] = Field(default_factory=list)


# --- Public API ---


def compute_fingerprint_for(
    config: AudienceDataConfig,
    input_data: AudienceDataInput,
) -> str:
    """Compute fingerprint for deduplication.

    Example:
        >>> fp = compute_fingerprint_for(config, input_data)
    """
    return compute_fingerprint(config, input_data)


def audience_data_workflow(
    config: AudienceDataConfig,
    input_data: AudienceDataInput,
    output_dir: Path,
    *,
    on_step: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """End-to-end audience data collection and analysis.

    Args:
        config: Workflow configuration.
        input_data: OAuth token or cookies.
        output_dir: Directory for outputs.
        on_step: Optional callback after each stage.

    Returns:
        Dict with: findings, fingerprint, decision_trail, report_path, cost_usd, status, error.

    Example:
        >>> result = audience_data_workflow(config, input_data, Path("out"))
        >>> assert result["status"] in ("completed", "failed")
    """
    import asyncio

    started_at = datetime.now(UTC)
    output_dir.mkdir(parents=True, exist_ok=True)
    cost_tracker = CostTracker()
    trail_steps: list[dict[str, Any]] = []
    fingerprint = compute_fingerprint_for(config, input_data)

    findings: AudienceDataFindings | None = None
    error: dict[str, Any] | None = None
    status: Literal["completed", "failed"] = "completed"

    try:
        findings = asyncio.run(
            _run_stages(config, input_data, output_dir, cost_tracker, trail_steps, on_step)
        )
    except Exception as exc:
        status = "failed"
        error = {"type": type(exc).__name__, "message": str(exc)}
        record_step(
            trail_steps=trail_steps, on_step=on_step, layer="oskill",
            callable_name="_pipeline_error",
            inputs_summary={}, outputs_summary={},
            started_at=datetime.now(UTC), status="failed", error=error,
        )

    decision_trail = build_decision_trail(
        fingerprint=fingerprint, config=config, input_data=input_data,
        trail_steps=trail_steps, cost_tracker=cost_tracker,
        started_at=started_at, status=status, error=error,
    )

    trail_path = output_dir / "decision_trail.json"
    trail_path.write_text(json.dumps(decision_trail, indent=2, default=str), encoding="utf-8")

    report_path: Path | None = None
    try:
        report_path = write_markdown_report(
            output_dir=output_dir, omodul_name="audience_data_workflow",
            fingerprint=fingerprint, config=config, findings=findings,
            decision_trail=decision_trail, cost_tracker=cost_tracker, status=status,
        )
    except Exception:
        pass

    return {
        "findings": findings,
        "fingerprint": fingerprint,
        "decision_trail": decision_trail,
        "report_path": report_path,
        "cost_usd": cost_tracker.total_usd,
        "status": status,
        "error": error,
    }


# --- Internal stages ---


async def _run_stages(
    config: AudienceDataConfig,
    input_data: AudienceDataInput,
    output_dir: Path,
    cost_tracker: CostTracker,
    trail_steps: list[dict[str, Any]],
    on_step: Callable[[dict[str, Any]], None] | None,
) -> AudienceDataFindings:
    """Run all stages sequentially."""
    from obase import ProviderRegistry

    llm = ProviderRegistry.get(category="llm", name=config.llm_provider)

    # Stage 1: Fetch stats
    t0 = datetime.now(UTC)
    stats_list = await _stage_fetch_stats(config, input_data)
    total_views = sum(s.get("views", 0) for s in stats_list)
    record_step(
        trail_steps=trail_steps, on_step=on_step, layer="oprim",
        callable_name="_stage_fetch_stats",
        inputs_summary={"videos": len(config.video_ids)},
        outputs_summary={"total_views": total_views},
        started_at=t0,
    )

    # Stage 2: Fetch comments
    t0 = datetime.now(UTC)
    all_comments = await _stage_fetch_comments(config, input_data)
    record_step(
        trail_steps=trail_steps, on_step=on_step, layer="oprim",
        callable_name="_stage_fetch_comments",
        inputs_summary={"videos": len(config.video_ids)},
        outputs_summary={"comments": len(all_comments)},
        started_at=t0,
    )

    # Stage 3: Sentiment analysis
    t0 = datetime.now(UTC)
    sentiment = await _stage_sentiment_analyze(all_comments, llm)
    record_step(
        trail_steps=trail_steps, on_step=on_step, layer="oprim",
        callable_name="_stage_sentiment_analyze",
        inputs_summary={"comments": len(all_comments)},
        outputs_summary={"positive_pct": sentiment.positive_pct},
        started_at=t0,
    )

    # Stage 4: Feedback extraction
    t0 = datetime.now(UTC)
    feedback = await _stage_feedback_extract(all_comments, llm)
    record_step(
        trail_steps=trail_steps, on_step=on_step, layer="oprim",
        callable_name="_stage_feedback_extract",
        inputs_summary={"comments": len(all_comments)},
        outputs_summary={"suggestions": len(feedback.suggestions)},
        started_at=t0,
    )

    # Stage 5: Learnings
    t0 = datetime.now(UTC)
    learnings = await _stage_learnings(stats_list, sentiment, feedback, llm)
    record_step(
        trail_steps=trail_steps, on_step=on_step, layer="oprim",
        callable_name="_stage_learnings",
        inputs_summary={},
        outputs_summary={"learnings": len(learnings)},
        started_at=t0,
    )

    return AudienceDataFindings(
        videos_analyzed=len(config.video_ids),
        total_views=total_views,
        total_comments=len(all_comments),
        sentiment_summary=SentimentSummary(
            positive_pct=sentiment.positive_pct,
            negative_pct=sentiment.negative_pct,
            neutral_pct=sentiment.neutral_pct,
            top_keywords=sentiment.top_keywords,
        ),
        feedback=FeedbackSummary(
            positive_points=feedback.positive_points,
            negative_points=feedback.negative_points,
            questions=feedback.questions,
            suggestions=feedback.suggestions,
        ),
        learnings=learnings,
    )


async def _stage_fetch_stats(
    config: AudienceDataConfig, input_data: AudienceDataInput
) -> list[dict[str, Any]]:
    """Fetch video stats for all video_ids."""
    results: list[dict[str, Any]] = []
    for vid in config.video_ids:
        if config.platform == "youtube":
            from oprim.youtube_video_stats import youtube_video_stats
            yt_stats = await youtube_video_stats(
                video_id=vid, oauth_token=input_data.oauth_token or "",
            )
            results.append(yt_stats.model_dump())
        else:
            from oprim.bilibili_video_stats import bilibili_video_stats
            bili_stats = await bilibili_video_stats(bvid=vid, cookies=input_data.cookies or {})
            results.append(bili_stats.model_dump())
    return results


async def _stage_fetch_comments(
    config: AudienceDataConfig, input_data: AudienceDataInput
) -> list[str]:
    """Fetch comments for all video_ids, return flat text list."""
    all_texts: list[str] = []
    for vid in config.video_ids:
        if config.platform == "youtube":
            from oprim.youtube_comments_fetch import youtube_comments_fetch
            yt_comments = await youtube_comments_fetch(
                video_id=vid, oauth_token=input_data.oauth_token or "",
                max_count=config.max_comments_per_video,
            )
            all_texts.extend(c.text for c in yt_comments)
        else:
            from oprim.bilibili_comments_fetch import bilibili_comments_fetch
            bili_comments = await bilibili_comments_fetch(
                bvid=vid, cookies=input_data.cookies or {},
                max_count=config.max_comments_per_video,
            )
            all_texts.extend(c.text for c in bili_comments)
    return all_texts


async def _stage_sentiment_analyze(comments: list[str], llm: Any) -> Any:
    """Run sentiment analysis on comments."""
    from oprim.audience_sentiment_analyze import SentimentResult, audience_sentiment_analyze

    if not comments:
        return SentimentResult(positive_pct=0, negative_pct=0, neutral_pct=1.0, top_keywords=[])
    return await audience_sentiment_analyze(comments=comments, llm=llm)


async def _stage_feedback_extract(comments: list[str], llm: Any) -> Any:
    """Extract structured feedback from comments."""
    from oprim.audience_feedback_extract import AudienceFeedback, audience_feedback_extract

    if not comments:
        return AudienceFeedback(
            positive_points=[], negative_points=[], questions=[], suggestions=[],
        )
    return await audience_feedback_extract(comments=comments, llm=llm)


async def _stage_learnings(
    stats_list: list[dict[str, Any]], sentiment: Any, feedback: Any, llm: Any,
) -> list[str]:
    """LLM extracts learnings from stats + sentiment + feedback."""
    import json as _json

    messages = [
        {"role": "system", "content": (
            "Based on video stats and audience feedback, extract 3-5 actionable learnings "
            "about what makes high-retention videos. Return JSON: [str, str, ...]"
        )},
        {"role": "user", "content": _json.dumps({
            "stats_summary": {
                "videos": len(stats_list),
                "total_views": sum(s.get("views", 0) for s in stats_list),
            },
            "sentiment": {"positive": sentiment.positive_pct, "negative": sentiment.negative_pct},
            "top_suggestions": feedback.suggestions[:5],
        }, default=str)},
    ]
    result = llm(messages=messages)
    content = result.get("content", "[]")
    try:
        parsed = _json.loads(content)
        return parsed if isinstance(parsed, list) else [str(parsed)]
    except Exception:
        return [content] if content else []
