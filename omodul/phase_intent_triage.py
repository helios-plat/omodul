"""omodul.phase_intent_triage — G0 intent brief from workspace snapshot."""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from obase.intent_brief import IntentBrief
from obase.workspace_snapshot import WorkspaceSnapshot
from oprim.boss_llm_callers import call_llm_for_intent
from oskill.context_assembler import assemble_intent_context
from oskill.leaf_contract import validate_intent_brief
from pydantic import BaseModel, ConfigDict

from omodul._base import BaseConfig, CostTracker, Trail, build_result, compute_fingerprint

_INTENT_SYSTEM = (
    "你是 Veya 意图分诊官（包工头）。不要写代码，不要拆任务。"
    "对照工作区截面理解用户要什么，只输出 JSON Intent Brief："
    '{"action":"plan|ask|refuse","interpretation":"",'
    '"in_scope_files":[],"out_of_scope_files":[],'
    '"acceptance_draft":[],"assumptions":[],"risks":[],'
    '"questions":[],"reasons":[]}。'
    "action=plan：interpretation 必须是一句话目标（不是用户原句），"
    "acceptance_draft 必须可被 git diff 或客观标准判定，questions 必须为空。"
    "action=ask：不清楚就停，questions 1-3 条，每条只问一件事，优先给选项。"
    "action=refuse：超出范围或破坏性且无授权，reasons 非空。"
)

_INTENT_USER = (
    "请产出 Intent Brief。能确信做什么、改哪些文件、如何验收就 plan；"
    "否则 ask。不要开始派工。"
)


class IntentConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "phase_intent_triage"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail", "cost"}
    _fingerprint_fields: ClassVar[set[str]] = {"goal_id"}

    goal_id: str = "boss"


class IntentInput(BaseModel):
    goal: str = ""
    snapshot: dict[str, Any] | WorkspaceSnapshot | None = None
    llm_caller: Any = None

    model_config = ConfigDict(extra="allow", arbitrary_types_allowed=True)


def compute_fingerprint_for(config: IntentConfig, input_data: IntentInput) -> str:
    return compute_fingerprint({"goal_id": config.goal_id, "goal": input_data.goal})


async def phase_intent_triage(
    config: IntentConfig | dict[str, Any],
    input_data: IntentInput | dict[str, Any],
    output_dir: Path,
    *,
    on_step=None,
) -> dict[str, Any]:
    """G0: snapshot + goal → IntentBrief. Fail = status failed, not an ask."""
    trail = Trail()
    cost = CostTracker()
    if not isinstance(config, IntentConfig):
        config = IntentConfig.model_validate(config or {})
    if not isinstance(input_data, IntentInput):
        input_data = IntentInput.model_validate(input_data or {})
    fp = compute_fingerprint_for(config, input_data)
    try:
        snapshot = _snapshot(input_data.snapshot)
        context_str = assemble_intent_context(snapshot, input_data.goal)
        trail.record(event="intent_context_assembled", step_no=1)
        caller = input_data.llm_caller
        if caller is None:
            extra = getattr(input_data, "__pydantic_extra__", None) or {}
            caller = extra.get("llm_caller")
        if caller is None:
            return build_result(
                status="failed",
                error={"type": "MissingLLMCaller", "message": "llm_caller is required"},
                trail=trail,
                fingerprint=fp,
                findings={},
            )
        messages = [
            {"role": "system", "content": f"{_INTENT_SYSTEM}\n当前环境特征：\n{context_str}"},
            {"role": "user", "content": _INTENT_USER},
        ]
        raw = await call_llm_for_intent(messages, caller=caller)
        cost.add_from_response(raw)
        brief = _to_brief(raw)
        errors = validate_intent_brief(brief)
        if errors:
            trail.record(event="intent_invalid", details=errors)
            return build_result(
                status="failed",
                error={
                    "type": "InvalidIntent",
                    "message": "Intent brief failed validation",
                    "details": errors,
                },
                trail=trail,
                fingerprint=fp,
                findings={"errors": errors},
                cost_usd=cost.total_usd,
            )
        trail.record(event="intent_ready", step_no=2, action=brief.action)
        if on_step:
            on_step({"action": brief.action})
        if output_dir:
            Path(output_dir).mkdir(parents=True, exist_ok=True)
            trail.write(Path(output_dir))
        return build_result(
            status="completed",
            error=None,
            trail=trail,
            fingerprint=fp,
            findings={"brief": brief.model_dump()},
            cost_usd=cost.total_usd,
        )
    except Exception as exc:
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
            trail=trail,
            fingerprint=fp,
            findings={},
            cost_usd=cost.total_usd,
        )


def _snapshot(raw: Any) -> WorkspaceSnapshot:
    if isinstance(raw, WorkspaceSnapshot):
        return raw
    if isinstance(raw, dict):
        return WorkspaceSnapshot.model_validate(raw)
    return WorkspaceSnapshot()


def _to_brief(raw: dict[str, Any]) -> IntentBrief:
    payload = {
        "action": raw.get("action") or "ask",
        "interpretation": raw.get("interpretation") or "",
        "in_scope_files": _str_list(raw.get("in_scope_files")),
        "out_of_scope_files": _str_list(raw.get("out_of_scope_files")),
        "acceptance_draft": _str_list(raw.get("acceptance_draft")),
        "assumptions": _str_list(raw.get("assumptions")),
        "risks": _str_list(raw.get("risks")),
        "questions": _str_list(raw.get("questions")),
        "reasons": _str_list(raw.get("reasons")),
    }
    return IntentBrief.model_validate(payload)


def _str_list(value: Any) -> list[str]:
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []
