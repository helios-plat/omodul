"""
omodul.run_subagent_task — Prepare a SubagentPlan for execution by E-5.

Pillars: decision_trail, cost
Correction 2: ONLY prepares the plan — does NOT execute it. Actual execution
is delegated to E-5 via an injected runner.
"""
from __future__ import annotations

import asyncio
import inspect
from contextvars import ContextVar
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from omodul._base import BaseConfig, CostTracker, Trail, build_result

_current_cost_m09: ContextVar[CostTracker] = ContextVar("_current_cost_m09")


async def _call(fn: Any, **kwargs: Any) -> Any:
    result = fn(**kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


class Config(BaseConfig):
    _omodul_name: ClassVar[str] = "run_subagent_task"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail", "cost"}

    max_depth: int = 3
    allowed_tools: list[str] = []


class InputData(BaseModel):
    task_description: str
    parent_cost_tracker: Any = None
    depth: int = 0
    dispatcher: Any = None

    model_config = ConfigDict(arbitrary_types_allowed=True)


async def run_subagent_task(
    config: Config,
    input_data: InputData,
    output_dir: Path,
    *,
    on_step=None,
) -> dict:
    """Prepare a SubagentPlan; does not execute — returns plan for E-5 runner."""
    # Use parent tracker if provided (same object ref, never replace)
    if input_data.parent_cost_tracker is not None:
        cost = input_data.parent_cost_tracker
    else:
        cost = CostTracker()
        _current_cost_m09.set(cost)

    trail = Trail()

    try:
        trail.record(event="prepare_plan", step_no=0,
                     task=input_data.task_description,
                     depth=input_data.depth)

        if input_data.depth >= config.max_depth:
            return build_result(
                status="failed",
                error={"type": "RecursionLimit",
                       "message": "max recursion depth exceeded"},
                trail=trail,
                cost_usd=cost.total_usd,
            )

        plan: dict = {
            "task": input_data.task_description,
            "allowed_tools": config.allowed_tools,
            "depth": input_data.depth,
        }

        if input_data.dispatcher is not None:
            dispatched = await _call(input_data.dispatcher,
                                     task=input_data.task_description,
                                     depth=input_data.depth)
            if dispatched:
                plan = dispatched

        trail.record(event="plan_ready", step_no=1, plan_keys=list(plan.keys()))

        loop = asyncio.get_event_loop()
        await asyncio.shield(loop.run_in_executor(None, trail.write, output_dir))

        return build_result(
            status="completed",
            error=None,
            trail=trail,
            cost_usd=cost.total_usd,
            plan=plan,
        )

    except asyncio.CancelledError:
        raise
    except Exception as exc:
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
            trail=trail,
            cost_usd=cost.total_usd,
        )
