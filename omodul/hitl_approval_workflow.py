"""omodul.hitl_approval_workflow — 人机交互审批事务.

集成：obase.hitl_signal_bus（审批请求 + 决策等待）+ oprim.hitl_wait_approval
（原子等待）+ decision_trail 全程留痕。

Pillars: fingerprint, decision_trail
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict

from omodul._base import BaseConfig, Trail, build_result, compute_fingerprint

ApprovalAction = Literal["request", "wait", "approve", "reject", "cancel", "status"]


class HitlApprovalConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "hitl_approval_workflow"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {"scope", "summary"}
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "decision_trail"}

    timeout: float = 60.0


class HitlApprovalInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    action: ApprovalAction = "request"
    scope: str = "default"          # 审批域（如 checkout / deploy / edit）
    summary: str = ""               # 审批摘要（人可见）
    request_id: str | None = None   # wait/approve/reject/cancel/status 时提供
    note: str = ""                  # 人工附注
    bus: Any = None                 # obase.hitl_signal_bus.HitlSignalBus 注入


def compute_fingerprint_for(config: HitlApprovalConfig, input_data: HitlApprovalInput) -> str:
    """Fingerprint over scope + summary + action。"""
    return compute_fingerprint(
        {"scope": input_data.scope, "summary": input_data.summary, "action": input_data.action}
    )


async def hitl_approval_workflow(
    config: HitlApprovalConfig,
    input_data: HitlApprovalInput,
    output_dir: Path,
    *,
    on_step=None,
) -> dict:
    """HITL 审批事务：请求 → 等待/人工决策 → 留痕。失败绝不 raise。"""
    trail = Trail()
    fp = compute_fingerprint_for(config, input_data)

    try:
        bus = input_data.bus
        if bus is None:
            from obase.hitl_signal_bus import get_bus

            bus = get_bus()
        if bus is None:
            raise ValueError(
                "no HITL bus available — inject input_data.bus or bind_bus() first"
            )

        action = input_data.action

        if action == "request":
            rid = bus.request_approval(
                scope=input_data.scope, summary=input_data.summary,
                request_id=input_data.request_id,
            )
            trail.record(event="hitl_request", request_id=rid, scope=input_data.scope)
            if on_step:
                on_step({"stage": "request", "request_id": rid})
            # request 动作默认进入等待（除非调用方只想建请求）
            decision = await bus.wait_for_decision(rid, timeout=config.timeout)
            trail.record(event="hitl_decision", request_id=rid, outcome=decision["outcome"])
            return build_result(
                status="completed",
                error=None,
                fingerprint=fp,
                trail=trail,
                trail_path=trail.write(Path(output_dir)) if output_dir else None,
                request_id=rid,
                decision=decision,
            )

        if action == "wait":
            if not input_data.request_id:
                raise ValueError("wait action requires request_id")
            from oprim import hitl_wait_approval

            decision = await hitl_wait_approval(
                input_data.request_id, bus=bus, timeout=config.timeout
            )
            trail.record(
                event="hitl_decision",
                request_id=input_data.request_id,
                outcome=decision["outcome"],
            )
            return build_result(
                status="completed",
                error=None,
                fingerprint=fp,
                trail=trail,
                trail_path=trail.write(Path(output_dir)) if output_dir else None,
                request_id=input_data.request_id,
                decision=decision,
            )

        if action in ("approve", "reject", "cancel"):
            if not input_data.request_id:
                raise ValueError(f"{action} action requires request_id")
            fn = getattr(bus, action)
            ok = fn(input_data.request_id, note=input_data.note)
            if not ok:
                raise ValueError(
                    f"{action} failed: request {input_data.request_id!r} "
                    "unknown or already decided"
                )
            trail.record(
                event=f"hitl_{action}", request_id=input_data.request_id, note=input_data.note
            )
            return build_result(
                status="completed",
                error=None,
                fingerprint=fp,
                trail=trail,
                trail_path=trail.write(Path(output_dir)) if output_dir else None,
                request_id=input_data.request_id,
                decided=True,
                decision=bus.get_request(input_data.request_id),
            )

        if action == "status":
            if not input_data.request_id:
                raise ValueError("status action requires request_id")
            info = bus.get_request(input_data.request_id)
            if info is None:
                raise ValueError(f"request {input_data.request_id!r} not found")
            trail.record(event="hitl_status", request_id=input_data.request_id)
            return build_result(
                status="completed",
                error=None,
                fingerprint=fp,
                trail=trail,
                trail_path=trail.write(Path(output_dir)) if output_dir else None,
                request_id=input_data.request_id,
                decision=info,
            )

        raise ValueError(f"unknown action: {action}")

    except Exception as exc:
        trail.record(event="error", detail=f"{type(exc).__name__}: {exc}")
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
            fingerprint=fp,
            trail=trail,
            trail_path=trail.write(Path(output_dir)) if output_dir else None,
        )
