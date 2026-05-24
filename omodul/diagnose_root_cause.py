import json
import traceback
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, Literal

from obase.cost_tracker import CostTracker
from obase.provider_registry import ProviderRegistry
from oskill import Signal
from oskill.agentic_investigate_loop import InvestigationOutcome, agentic_investigate_loop
from pydantic import BaseModel, Field

from omodul._base_config import BaseConfig
from omodul._decision_trail import build_decision_trail, record_step
from omodul._fingerprint import compute_fingerprint
from omodul._report import write_markdown_report
from omodul._runtime import _current_cost_tracker


class DiagnoseRootCauseConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "diagnose_root_cause"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {
        "signal_hash", "available_tools_hash", "max_steps"
    }
    llm_model: str = "claude-3-5-sonnet-20241022"
    max_steps: int = 20
    max_tokens_per_step: int = 4096
    confidence_threshold: float = 0.8
    signal_hash: str
    available_tools_hash: str


class DiagnoseRootCauseInput(BaseModel):
    signal: Signal
    available_tool_names: list[str]    # e.g. ["docker_container_logs", "postgres_slow_queries", ...]
    initial_context: dict[str, Any] = Field(default_factory=dict)


class DiagnoseRootCauseFindings(BaseModel):
    root_cause_hypothesis: str
    evidence_chain: list[dict[str, Any]]         # 每步证据 (step_no / tool_used / observation / inference)
    confidence: float
    suggested_actions: list[str]       # 自然语言建议
    requires_human: bool
    requires_human_reason: str | None = None


def diagnose_root_cause(
    config: DiagnoseRootCauseConfig,
    input_data: DiagnoseRootCauseInput,
    output_dir: Path,
    *,
    on_step: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """agentic 多步根因调查."""
    started_at = datetime.now(UTC)
    fingerprint = compute_fingerprint(config, input_data)
    cost_tracker = CostTracker(budget_usd=config.budget_usd)
    trail_steps: list[dict[str, Any]] = []
    error_info = None
    status: Literal["completed", "failed"] = "completed"
    findings = None

    token = _current_cost_tracker.set(cost_tracker)
    try:
        # stage 1: Agentic Investigation
        outcome = _stage_investigation(config, input_data, trail_steps, on_step)

        # stage 2: Finalize findings
        findings = DiagnoseRootCauseFindings(
            root_cause_hypothesis=outcome.final_conclusion.get("root_cause_hypothesis", "Unknown"),
            evidence_chain=[s.model_dump() for s in outcome.steps],
            confidence=outcome.final_conclusion.get("confidence", 0.0),
            suggested_actions=outcome.final_conclusion.get("suggested_actions", []),
            requires_human=outcome.stopped_reason == "max_steps" or outcome.final_conclusion.get("confidence", 0.0) < config.confidence_threshold,
            requires_human_reason=f"Stopped due to {outcome.stopped_reason}" if outcome.stopped_reason != "confidence_threshold" else None
        )
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
        status=status
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


def _stage_investigation(
    config: DiagnoseRootCauseConfig,
    input_data: DiagnoseRootCauseInput,
    trail_steps: list[dict[str, Any]],
    on_step: Callable[[dict[str, Any]], None] | None
) -> InvestigationOutcome:
    started_at = datetime.now(UTC)

    provider = ProviderRegistry.get(config.llm_provider)
    llm = provider.create_caller(model=config.llm_model)

    outcome = agentic_investigate_loop(
        signal=input_data.signal,
        available_tool_names=input_data.available_tool_names,
        llm=llm,
        on_step=on_step,
        max_steps=config.max_steps,
        confidence_threshold=config.confidence_threshold
    )

    record_step(
        trail_steps=trail_steps,
        on_step=on_step,
        layer="oskill",
        callable_name="agentic_investigate_loop",
        inputs_summary={"signal_hash": config.signal_hash, "tools_count": len(input_data.available_tool_names)},
        outputs_summary={"steps_taken": outcome.steps_taken, "stopped_reason": outcome.stopped_reason},
        started_at=started_at
    )

    return outcome


def compute_fingerprint_for_diagnose_root_cause(config: DiagnoseRootCauseConfig, input_data: DiagnoseRootCauseInput) -> str:
    return compute_fingerprint(config, input_data)
