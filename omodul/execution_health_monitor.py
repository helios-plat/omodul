"""omodul.execution_health_monitor — L1/L2/L3 agent-loop breaker."""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from obase.loop_breaker import get_breaker
from omodul._base import BaseConfig, Trail, build_result
from oprim._hash_tool_call import hash_tool_call
from oskill.constitutional_violation import detect_constitution_violation
from oskill.loop_detection import detect_trajectory_loop
from pydantic import BaseModel, ConfigDict


class MonitorConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "execution_health_monitor"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail"}

    max_consecutive_errors: int = 3
    max_steps_per_turn: int = 25
    loop_window_size: int = 4


class MonitorInput(BaseModel):
    tool_name: str
    arguments: dict[str, Any] = {}
    is_error: bool = False
    execution_log: str = ""
    constitution: str | list[str] = ""

    model_config = ConfigDict(extra="allow")


def compute_fingerprint_for(config: MonitorConfig, input_data: MonitorInput) -> str:
    return "health_monitor_volatile"


async def execution_health_monitor(
    config: MonitorConfig,
    input_data: MonitorInput | dict[str, Any],
    output_dir: Path,
    *,
    on_step=None,
) -> dict[str, Any]:
    """Score the current tool attempt. Never raises."""
    trail = Trail()
    if not isinstance(config, MonitorConfig):
        config = MonitorConfig.model_validate(config or {})
    if isinstance(input_data, MonitorInput):
        data = input_data
    else:
        data = MonitorInput.model_validate(input_data)
    fp = compute_fingerprint_for(config, data)
    state = get_breaker()
    if state is None:
        return build_result(
            status="failed",
            error={
                "type": "BreakerNotInitialized",
                "message": "Breaker ContextVar not initialized",
            },
            trail=trail,
            fingerprint=fp,
            findings={"action": "halt", "intervention_prompt": None},
            decision_events=[],
        )

    findings: dict[str, Any] = {"action": "continue", "intervention_prompt": None}
    events: list[dict[str, Any]] = []
    try:
        violation = None
        if data.execution_log and data.constitution:
            violation = detect_constitution_violation(
                data.execution_log, constitution_rules=data.constitution
            )
        if violation:
            findings["action"] = "intervene"
            findings["intervention_prompt"] = (
                f"[SYSTEM SHIELD] 你违反了项目宪法：{violation}。请立刻修正执行路径，不得重犯。"
            )
            events.append({"type": "CONSTITUTION_Breaker_Triggered", "reason": violation})
            trail.record(event="constitution_intervene", reason=violation)
            if on_step:
                on_step({"action": "intervene", "reason": violation})
            if output_dir:
                Path(output_dir).mkdir(parents=True, exist_ok=True)
                trail.write(Path(output_dir))
            return build_result(
                status="completed",
                error=None,
                trail=trail,
                fingerprint=fp,
                findings=findings,
                decision_events=events,
                total_steps=state.total_steps if state else 0,
            )

        state.total_steps += 1
        if data.is_error:
            state.consecutive_errors += 1
        else:
            state.consecutive_errors = 0

        digest = hash_tool_call(data.tool_name, arguments=data.arguments)
        state.trajectory_hashes.append(digest)

        if state.total_steps > config.max_steps_per_turn:
            findings["action"] = "halt"
            events.append({"type": "L3_Breaker_Triggered", "reason": "max_steps_exceeded"})
            trail.record(event="L3_halt", step_no=state.total_steps, reason="max_steps_exceeded")
        elif state.consecutive_errors >= config.max_consecutive_errors:
            findings["action"] = "intervene"
            findings["intervention_prompt"] = (
                "[SYSTEM SHIELD] 你已连续 "
                f"{state.consecutive_errors} 次尝试失败。强制要求：转换解决思路，"
                "禁止重复之前的操作。若无法解决请调用 ask_user。"
            )
            events.append({"type": "L1_Breaker_Triggered", "reason": "consecutive_errors"})
            trail.record(event="L1_intervene", step_no=state.total_steps)
        elif detect_trajectory_loop(state.trajectory_hashes, window_size=config.loop_window_size):
            findings["action"] = "intervene"
            findings["intervention_prompt"] = (
                "[SYSTEM SHIELD] 侦测到循环调用模式（死锁）。强制要求重新评估上下文代码。"
            )
            events.append({"type": "L2_Breaker_Triggered", "reason": "trajectory_loop"})
            trail.record(event="L2_intervene", step_no=state.total_steps)

        if on_step:
            on_step({"action": findings["action"], "steps": state.total_steps})

        if output_dir:
            Path(output_dir).mkdir(parents=True, exist_ok=True)
            trail.write(Path(output_dir))

        return build_result(
            status="completed",
            error=None,
            trail=trail,
            fingerprint=fp,
            findings=findings,
            decision_events=events,
            total_steps=state.total_steps,
        )
    except Exception as exc:
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
            trail=trail,
            fingerprint=fp,
            findings=findings,
            decision_events=events,
        )
