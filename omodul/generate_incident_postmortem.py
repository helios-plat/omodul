import json
import traceback
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, Literal

from obase.cost_tracker import CostTracker
from obase.provider_registry import ProviderRegistry
from oskill.event_trail_correlate import CorrelatedEvents, event_trail_correlate
from pydantic import BaseModel

from omodul._base_config import BaseConfig
from omodul._decision_trail import build_decision_trail, record_step
from omodul._fingerprint import compute_fingerprint
from omodul._report import write_markdown_report
from omodul._runtime import _current_cost_tracker


class GenerateIncidentPostmortemConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "generate_incident_postmortem"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {
        "incident_id", "time_window", "scope"
    }
    llm_model: str = "claude-3-5-sonnet-20241022"
    time_window: str                   # ISO 8601 interval, e.g. "2026-05-20T10:00Z/2026-05-20T12:00Z"
    scope: Literal["timeline_only", "with_analysis", "full"] = "full"
    max_events_in_timeline: int = 100
    incident_id: str


class GenerateIncidentPostmortemInput(BaseModel):
    incident_id: str
    event_trail: list[dict[str, Any]]            # 服务层从 Postgres 查好传入
    involved_services: list[str]
    resolutions_applied: list[dict[str, Any]]


class GenerateIncidentPostmortemFindings(BaseModel):
    incident_id: str
    timeline: list[dict[str, Any]]
    root_cause_analysis: str           # markdown 段落
    contributing_factors: list[str]
    action_items: list[dict[str, Any]]           # [{action, owner_hint, priority}]
    lessons_learned: list[str]
    impact_summary: str


def generate_incident_postmortem(
    config: GenerateIncidentPostmortemConfig,
    input_data: GenerateIncidentPostmortemInput,
    output_dir: Path,
    *,
    on_step: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """生成事后报告."""
    started_at = datetime.now(UTC)
    fingerprint = compute_fingerprint(config, input_data)
    cost_tracker = CostTracker(budget_usd=config.budget_usd)
    trail_steps: list[dict[str, Any]] = []
    error_info = None
    status: Literal["completed", "failed"] = "completed"
    findings = None

    token = _current_cost_tracker.set(cost_tracker)
    try:
        # stage 1: Correlate Events
        correlated = _stage_correlate_events(config, input_data, trail_steps, on_step)

        # stage 2: Synthesize Postmortem (LLM)
        findings = _stage_synthesize_postmortem(config, input_data, correlated, trail_steps, on_step)

    except Exception as e:
        error_info = {
            "error_class": type(e).__name__,
            "error_message": str(e),
            "traceback": traceback.format_exc(),
        }
        status = "failed"
    finally:
        _current_cost_tracker.reset(token)

    decision_trail = build_decision_trail(
        fingerprint=fingerprint, config=config,
        input_data=input_data, trail_steps=trail_steps,
        cost_tracker=cost_tracker, started_at=started_at,
        status=status, error=error_info,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "decision_trail.json").write_text(
        json.dumps(decision_trail, indent=2, ensure_ascii=False, default=str)
    )

    report_path = write_markdown_report(
        output_dir=output_dir,
        omodul_name=config._omodul_name,
        fingerprint=fingerprint,
        config=config,
        findings=findings,
        decision_trail=decision_trail,
        cost_tracker=cost_tracker,
        status=status,
        custom_findings_section=_custom_postmortem_section
    )

    return {
        "findings": findings,
        "fingerprint": fingerprint,
        "decision_trail": decision_trail,
        "report_path": report_path,
        "cost_usd": cost_tracker.total_usd,
        "status": status,
        "error": error_info,
    }


def _stage_correlate_events(
    config: GenerateIncidentPostmortemConfig,
    input_data: GenerateIncidentPostmortemInput,
    trail_steps: list[dict[str, Any]],
    on_step: Callable[[dict[str, Any]], None] | None
) -> CorrelatedEvents:
    started_at = datetime.now(UTC)
    # Use the first event as target for correlation if not specified
    target_id = str(input_data.event_trail[0].get("id")) if input_data.event_trail else "unknown"
    result = event_trail_correlate(
        target_event_id=target_id,
        all_events=input_data.event_trail
    )
    record_step(
        trail_steps=trail_steps,
        on_step=on_step,
        layer="oskill",
        callable_name="event_trail_correlate",
        inputs_summary={"incident_id": config.incident_id},
        outputs_summary={"correlated_count": len(result.causally_related)},
        started_at=started_at
    )
    return result


def _stage_synthesize_postmortem(
    config: GenerateIncidentPostmortemConfig,
    input_data: GenerateIncidentPostmortemInput,
    correlated: CorrelatedEvents,
    trail_steps: list[dict[str, Any]],
    on_step: Callable[[dict[str, Any]], None] | None
) -> GenerateIncidentPostmortemFindings:
    started_at = datetime.now(UTC)

    if config.scope == "timeline_only":
        return GenerateIncidentPostmortemFindings(
            incident_id=config.incident_id,
            timeline=input_data.event_trail[:config.max_events_in_timeline],
            root_cause_analysis="Analysis skipped (timeline only).",
            contributing_factors=[],
            action_items=[],
            lessons_learned=[],
            impact_summary="Summary skipped (timeline only)."
        )

    provider = ProviderRegistry.get(config.llm_provider)
    llm = provider.create_caller(model=config.llm_model)

    prompt = f"""Generate an incident postmortem for incident {config.incident_id}.
    
Timeline of events:
{json.dumps(input_data.event_trail[:config.max_events_in_timeline], indent=2)}

Resolutions applied:
{json.dumps(input_data.resolutions_applied, indent=2)}

Involved services: {", ".join(input_data.involved_services)}

Provide:
1. Root Cause Analysis
2. Contributing Factors
3. Action Items (priority, action, owner hint)
4. Lessons Learned
5. Impact Summary
"""
    response = llm(
        messages=[{"role": "user", "content": prompt}],
        max_tokens=4096
    )

    content = str(response.get("content", ""))

    # In a real omodul, we would parse the LLM response more structuredly.
    findings = GenerateIncidentPostmortemFindings(
        incident_id=config.incident_id,
        timeline=input_data.event_trail[:config.max_events_in_timeline],
        root_cause_analysis=content,
        contributing_factors=[],
        action_items=[],
        lessons_learned=[],
        impact_summary=""
    )

    record_step(
        trail_steps=trail_steps,
        on_step=on_step,
        layer="oskill",
        callable_name="llm_synthesize_postmortem",
        inputs_summary={"scope": config.scope},
        outputs_summary={"content_len": len(content)},
        started_at=started_at
    )

    return findings


def _custom_postmortem_section(findings: GenerateIncidentPostmortemFindings | None) -> str:
    if findings is None:
        return "## 3. Findings\n\nNo findings available (analysis failed)."
    timeline_lines = [f"- {e.get('timestamp')}: {e.get('event') or e.get('msg')}" for e in findings.timeline]
    timeline = "\n".join(timeline_lines)

    return f"""## 3. Incident Postmortem Findings

### Timeline
{timeline}

### Root Cause Analysis
{findings.root_cause_analysis}
"""


def compute_fingerprint_for_generate_incident_postmortem(config: GenerateIncidentPostmortemConfig, input_data: GenerateIncidentPostmortemInput) -> str:
    return compute_fingerprint(config, input_data)
