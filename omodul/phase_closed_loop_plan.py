"""omodul.phase_closed_loop_plan — G1 closed-loop task graph from snapshot."""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from obase.workspace_snapshot import WorkspaceSnapshot
from oprim.boss_llm_callers import call_llm_for_planning
from oskill.context_assembler import assemble_boss_context
from oskill.dag_validator import validate_taskgraph_dag
from oskill.leaf_contract import validate_leaf_contract
from pydantic import BaseModel, ConfigDict

from omodul._base import BaseConfig, CostTracker, Trail, build_result, compute_fingerprint

_PLAN_SYSTEM = (
    "你是 Veya 宏观架构师（包工头）。不要亲自写代码。"
    "只按 Intent Brief 拆成叶子任务，不要重新解释用户原话。"
    "每个任务必须有 files[]、logic、forbidden[]、acceptance[]、"
    "assignee(hicode|dsh|ask)。"
    "acceptance 必须可用 git diff 或客观标准判定。"
    '只输出 JSON：{"tasks":[{"id","title","files":[],"logic":"",'
    '"forbidden":[],"acceptance":[],"depends_on":[],"assignee":"hicode"}]}。'
)

_PLAN_USER = (
    "按 Intent Brief 派工。每个叶子只做一件事。"
    "files / logic / forbidden / acceptance 不得为空。"
    "代码改动派 hicode，检索或浏览派 dsh，必须人确认派 ask。"
)

_ALLOWED_ASSIGNEES = frozenset({"hicode", "dsh", "ask"})


class PlanConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "phase_closed_loop_plan"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail", "cost"}
    _fingerprint_fields: ClassVar[set[str]] = {"goal_id"}

    goal_id: str
    default_assignee: str = "hicode"
    max_leaf_tasks: int = 40


class PlanInput(BaseModel):
    goal: str = ""
    snapshot: dict[str, Any] | WorkspaceSnapshot | None = None
    intent_brief: dict[str, Any] | None = None
    llm_caller: Any = None

    model_config = ConfigDict(extra="allow", arbitrary_types_allowed=True)


def compute_fingerprint_for(config: PlanConfig, input_data: PlanInput) -> str:
    return compute_fingerprint({"goal_id": config.goal_id, "goal": input_data.goal})


async def phase_closed_loop_plan(
    config: PlanConfig | dict[str, Any],
    input_data: PlanInput | dict[str, Any],
    output_dir: Path,
    *,
    on_step=None,
) -> dict[str, Any]:
    """G1: snapshot + goal → validated closed-loop task graph. Fail = status failed."""
    trail = Trail()
    cost = CostTracker()
    if not isinstance(config, PlanConfig):
        config = PlanConfig.model_validate(config)
    if not isinstance(input_data, PlanInput):
        input_data = PlanInput.model_validate(input_data or {})
    fp = compute_fingerprint_for(config, input_data)
    try:
        snapshot = _snapshot(input_data.snapshot)
        context_str = assemble_boss_context(
            snapshot, input_data.goal, brief=input_data.intent_brief
        )
        trail.record(event="context_assembled", step_no=1)
        if on_step:
            on_step({"step": 1, "action": "context_assembled"})
        caller = input_data.llm_caller or getattr(input_data, "model_extra", {}).get(
            "llm_caller"
        )
        if caller is None and isinstance(input_data, PlanInput):
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
            {"role": "system", "content": f"{_PLAN_SYSTEM}\n当前环境特征：\n{context_str}"},
            {"role": "user", "content": _PLAN_USER},
        ]
        raw_plan = await call_llm_for_planning(messages, caller=caller)
        cost.add_from_response(raw_plan)
        trail.record(event="plan_generated", step_no=2)
        graph = _normalize_graph(raw_plan, config)
        errors = validate_taskgraph_dag(graph)
        if not errors:
            errors = validate_leaf_contract(graph)
        if not errors and len(graph["tasks"]) > config.max_leaf_tasks:
            errors = [f"too many tasks: {len(graph['tasks'])} > {config.max_leaf_tasks}"]
        if errors:
            trail.record(event="dag_validation_failed", details=errors)
            err_type = (
                "InvalidLeafContract"
                if any("missing " in e or "invalid assignee" in e for e in errors)
                else "InvalidDAG"
            )
            return build_result(
                status="failed",
                error={
                    "type": err_type,
                    "message": "DAG validation failed",
                    "details": errors,
                },
                trail=trail,
                fingerprint=fp,
                findings={"errors": errors},
                cost_usd=cost.total_usd,
            )
        trail.record(event="dag_validated", step_no=3, task_count=len(graph["tasks"]))
        if on_step:
            on_step({"step": 3, "task_count": len(graph["tasks"])})
        if output_dir:
            Path(output_dir).mkdir(parents=True, exist_ok=True)
            trail.write(Path(output_dir))
        return build_result(
            status="completed",
            error=None,
            trail=trail,
            fingerprint=fp,
            findings={"graph": graph, "task_count": len(graph["tasks"])},
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


def _normalize_graph(raw_plan: dict[str, Any], config: PlanConfig) -> dict[str, Any]:
    tasks = raw_plan.get("tasks")
    if tasks is None and isinstance(raw_plan.get("graph"), dict):
        tasks = raw_plan["graph"].get("tasks")
    if not isinstance(tasks, list):
        tasks = []
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(tasks, start=1):
        if not isinstance(item, dict):
            continue
        ident = str(item.get("id") or f"T{index}")
        title = str(item.get("title") or ident)
        files = _str_list(item.get("files"))
        logic = str(item.get("logic") or "").strip()
        forbidden = _str_list(item.get("forbidden"))
        acceptance = _str_list(item.get("acceptance"))
        depends = item.get("depends_on") or []
        if isinstance(depends, str):
            depends = [p.strip() for p in depends.split(",") if p.strip()]
        assignee = str(item.get("assignee") or "").strip()
        if assignee not in _ALLOWED_ASSIGNEES:
            assignee = config.default_assignee
        instruction = _compose_instruction(
            raw=str(item.get("instruction") or ""),
            files=files,
            logic=logic,
            forbidden=forbidden,
            title=title,
        )
        normalized.append(
            {
                "id": ident,
                "title": title,
                "instruction": instruction,
                "files": files,
                "logic": logic,
                "forbidden": forbidden,
                "acceptance": list(acceptance),
                "depends_on": list(depends),
                "assignee": assignee,
                "status": "pending",
                "retries": 0,
            }
        )
    return {"version": 1, "goal_id": config.goal_id, "tasks": normalized}


def _str_list(value: Any) -> list[str]:
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _compose_instruction(
    *,
    raw: str,
    files: list[str],
    logic: str,
    forbidden: list[str],
    title: str,
) -> str:
    parts: list[str] = []
    if files:
        parts.append("Files: " + ", ".join(files))
    if logic:
        parts.append("Logic: " + logic)
    if forbidden:
        parts.append("Forbidden: " + "; ".join(forbidden))
    body = raw.strip() or title
    if body:
        parts.append(body)
    return "\n".join(parts)
