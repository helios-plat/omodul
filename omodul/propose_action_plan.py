import json
import traceback
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, Literal

from obase.cost_tracker import CostTracker
from obase.provider_registry import ProviderRegistry
from oskill.retrieve_and_synthesize import RetrievedDoc, SynthesizedResult, retrieve_and_synthesize
from oskill.runbook_match import RunbookMatchResult, runbook_match
from pydantic import BaseModel, Field

from omodul._base_config import BaseConfig
from omodul._decision_trail import build_decision_trail, record_step
from omodul._fingerprint import compute_fingerprint
from omodul._report import write_markdown_report
from omodul._runtime import _current_cost_tracker


class ProposeActionPlanConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "propose_action_plan"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {
        "root_cause_hash", "plugin_marketplace_version"
    }
    llm_model: str = "claude-3-5-sonnet-20241022"
    top_k_runbooks: int = 5
    root_cause_hash: str
    plugin_marketplace_version: str


class ProposeActionPlanInput(BaseModel):
    root_cause: dict[str, Any]                   # diagnose_root_cause 输出 findings
    available_plugins: list[dict[str, Any]]      # marketplace plugin index (含 metadata + matcher 规则)
    historical_resolutions: list[dict[str, Any]] = Field(default_factory=list)


class ProposeActionPlanFindings(BaseModel):
    action_plan: list[dict[str, Any]]            # [{step: 1, action: "...", expected_outcome: "..."}]
    matched_plugin: dict[str, Any] | None = None        # 直接匹配的 plugin (若有)
    risk: Literal["low", "medium", "high", "critical"]
    required_approval_level: Literal["auto", "user_approval", "admin_approval"]
    confidence: float
    fallback_action: str | None = None        # 如执行失败的兜底动作


def propose_action_plan(
    config: ProposeActionPlanConfig,
    input_data: ProposeActionPlanInput,
    output_dir: Path,
    *,
    on_step: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """根因 → action plan + plugin 匹配."""
    started_at = datetime.now(UTC)
    fingerprint = compute_fingerprint(config, input_data)
    cost_tracker = CostTracker(budget_usd=config.budget_usd)
    trail_steps: list[dict[str, Any]] = []
    error_info = None
    status: Literal["completed", "failed"] = "completed"
    findings = None

    token = _current_cost_tracker.set(cost_tracker)
    try:
        # stage 1: Runbook Match
        match_result = _stage_runbook_match(config, input_data, trail_steps, on_step)

        # stage 2: Synthesize Action Plan
        synth_result = _stage_synthesize_plan(config, input_data, trail_steps, on_step)

        # stage 3: Finalize
        findings = _stage_finalize_findings(match_result, synth_result)

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


def _stage_runbook_match(
    config: ProposeActionPlanConfig,
    input_data: ProposeActionPlanInput,
    trail_steps: list[dict[str, Any]],
    on_step: Callable[[dict[str, Any]], None] | None
) -> RunbookMatchResult:
    started_at = datetime.now(UTC)
    result = runbook_match(
        root_cause=input_data.root_cause,
        available_plugins=input_data.available_plugins
    )
    record_step(
        trail_steps=trail_steps,
        on_step=on_step,
        layer="oskill",
        callable_name="runbook_match",
        inputs_summary={"rc_hash": config.root_cause_hash},
        outputs_summary={"matched": result.matched_plugin is not None, "score": result.match_score},
        started_at=started_at
    )
    return result


def _stage_synthesize_plan(
    config: ProposeActionPlanConfig,
    input_data: ProposeActionPlanInput,
    trail_steps: list[dict[str, Any]],
    on_step: Callable[[dict[str, Any]], None] | None
) -> SynthesizedResult:
    started_at = datetime.now(UTC)

    provider = ProviderRegistry.get(config.llm_provider)
    llm = provider.create_caller(model=config.llm_model)

    # We don't have a vector_search_fn here, so we'll mock one for historical_resolutions
    def local_search(q: str, c: str, k: int) -> list[RetrievedDoc]:
        return [RetrievedDoc(doc_id=str(i), content=str(r), score=1.0) for i, r in enumerate(input_data.historical_resolutions[:k])]

    result = retrieve_and_synthesize(
        query=f"Action plan for root cause: {input_data.root_cause.get('root_cause_hypothesis')}",
        corpus_id="historical_resolutions",
        llm=llm,
        top_k=config.top_k_runbooks,
        vector_search_fn=local_search
    )

    record_step(
        trail_steps=trail_steps,
        on_step=on_step,
        layer="oskill",
        callable_name="retrieve_and_synthesize",
        inputs_summary={"query_len": len(str(input_data.root_cause))},
        outputs_summary={"confidence": result.confidence},
        started_at=started_at
    )
    return result


def _stage_finalize_findings(match_result: RunbookMatchResult, synth_result: SynthesizedResult) -> ProposeActionPlanFindings:
    # Logic to merge match and synth results
    # If a plugin is matched with high score, it takes precedence.

    action_plan: list[dict[str, Any]] = []
    if match_result.matched_plugin:
        # Assuming plugin has a 'steps' field
        action_plan = match_result.matched_plugin.get("steps", [])
        risk_val = match_result.matched_plugin.get("risk", "medium")
        approval: Literal["auto", "user_approval", "admin_approval"] = "auto" if match_result.match_score > 0.9 and risk_val == "low" else "user_approval"
        confidence = match_result.match_score
        risk: Literal["low", "medium", "high", "critical"] = risk_val if risk_val in ("low", "medium", "high", "critical") else "medium"
    else:
        # Fallback to synthesized plan from LLM
        action_plan = [{"step": 1, "action": synth_result.synthesized_answer}]
        risk = "medium"
        approval = "user_approval"
        confidence = synth_result.confidence

    return ProposeActionPlanFindings(
        action_plan=action_plan,
        matched_plugin=match_result.matched_plugin,
        risk=risk,
        required_approval_level=approval,
        confidence=confidence
    )


def compute_fingerprint_for_propose_action_plan(config: ProposeActionPlanConfig, input_data: ProposeActionPlanInput) -> str:
    return compute_fingerprint(config, input_data)
