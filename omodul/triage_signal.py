import json
import re
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal, ClassVar, Any, Set

from obase.cost_tracker import CostTracker
from obase.provider_registry import ProviderRegistry
from omodul._base_config import BaseConfig
from omodul._decision_trail import build_decision_trail, record_step
from omodul._fingerprint import compute_fingerprint
from omodul._report import write_markdown_report
from omodul._runtime import _current_cost_tracker
from oskill import Signal
from pydantic import BaseModel, Field


class TriageSignalConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "triage_signal"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[Set[str]] = {
        "signal_hash", "context_hash"
    }
    llm_model: str = "claude-3-haiku-20240307"   # Triage 用轻量
    max_tokens: int = 500
    signal_hash: str                   # caller 算好传入
    context_hash: str


class TriageSignalInput(BaseModel):
    signal: Signal                     # 通用 Signal, 不是 AegisAlert
    context: dict[str, Any] = Field(default_factory=dict)                 # 历史 / 关联事件 (caller 提供)


class TriageSignalFindings(BaseModel):
    priority: Literal["P0", "P1", "P2", "P3"]
    category: str                      # caller 定义的分类名 (e.g. "infra-db", "user-error")
    should_escalate: bool
    escalate_to: str | None = None            # e.g. "rca_agent" / "human_review"
    routing_hint: str
    confidence: float                  # 0-1
    reasoning_summary: str             # LLM 给的简短理由 (≤ 200 字)


def triage_signal(
    config: TriageSignalConfig,
    input_data: TriageSignalInput,
    output_dir: Path,
    *,
    on_step: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """对单个信号做轻量分诊 (LLM Haiku)."""
    started_at = datetime.now(timezone.utc)
    fingerprint = compute_fingerprint(config, input_data)
    cost_tracker = CostTracker(budget_usd=config.budget_usd)
    trail_steps: list[dict[str, Any]] = []
    error_info = None
    status: Literal["completed", "failed"] = "completed"
    findings = None

    token = _current_cost_tracker.set(cost_tracker)
    try:
        # stage 1: LLM Triage
        findings = _stage_llm_triage(config, input_data, trail_steps, on_step)
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


def _stage_llm_triage(
    config: TriageSignalConfig, 
    input_data: TriageSignalInput, 
    trail_steps: list[dict[str, Any]], 
    on_step: Callable[[dict[str, Any]], None] | None
) -> TriageSignalFindings:
    started_at = datetime.now(timezone.utc)
    
    provider = ProviderRegistry.get(config.llm_provider)
    llm = provider.create_caller(model=config.llm_model)
    
    prompt = f"""You are a signal triage assistant. Given a signal and context, classify priority and route it.

Signal:
{input_data.signal.model_dump_json(indent=2)}

Context:
{json.dumps(input_data.context, indent=2)}

Respond in strict JSON:
{{
  "priority": "P0|P1|P2|P3",
  "category": "string",
  "should_escalate": bool,
  "escalate_to": "rca_agent|human_review|null",
  "routing_hint": "string",
  "confidence": 0.0-1.0,
  "reasoning_summary": "string ≤ 200 chars"
}}
"""
    response = llm(
        messages=[{"role": "user", "content": prompt}],
        max_tokens=config.max_tokens
    )
    
    # Parse JSON from LLM response
    content = response.get("content", "")
    # Try to find JSON in the response
    match = re.search(r"\{.*\}", str(content), re.DOTALL)
    if match:
        data = json.loads(match.group(0))
    else:
        data = json.loads(str(content))
        
    findings = TriageSignalFindings(**data)
    
    record_step(
        trail_steps=trail_steps,
        on_step=on_step,
        layer="oskill", 
        callable_name="llm_triage",
        inputs_summary={"signal_hash": config.signal_hash},
        outputs_summary={"priority": findings.priority, "category": findings.category},
        started_at=started_at
    )
    
    return findings


def compute_fingerprint_for_triage_signal(config: TriageSignalConfig, input_data: TriageSignalInput) -> str:
    return compute_fingerprint(config, input_data)
