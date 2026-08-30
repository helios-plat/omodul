"""Canonical Action Gateway transactions.

The transactions compose injected policy/audit/execution primitives.  They do
not own a policy store, approval store, side-effect ledger, or tool registry.
"""

from __future__ import annotations

import contextlib
import inspect
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, ClassVar

from obase.action import ActionDecision, ActionRequest, AuditRecord
from oprim._action import tool_invoke
from pydantic import BaseModel, ConfigDict

from omodul._base import BaseConfig, Trail, build_result, compute_fingerprint


async def _call(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    result = fn(*args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


def _decision(value: Any, request_id: str) -> ActionDecision:
    if isinstance(value, ActionDecision):
        if value.request_id:
            return value
        return ActionDecision(
            verdict=value.verdict,
            reason=value.reason,
            policy_id=value.policy_id,
            approved=value.approved,
            request_id=request_id,
        )
    if isinstance(value, Mapping):
        raw = str(value.get("verdict", value.get("decision", "DENY"))).upper()
        verdict = raw if raw in {"ALLOW", "DENY", "REQUIRE_APPROVAL"} else "DENY"
        return ActionDecision(
            verdict=verdict,  # type: ignore[arg-type]
            reason=str(value.get("reason", "")),
            policy_id=value.get("policy_id"),
            approved=bool(value.get("approved", False)),
            request_id=request_id,
        )
    raw = str(value).upper()
    verdict = raw if raw in {"ALLOW", "DENY", "REQUIRE_APPROVAL"} else "DENY"
    return ActionDecision(verdict=verdict, request_id=request_id)  # type: ignore[arg-type]


class GovernActionConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "govern_action"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail"}


class GovernActionInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    request: ActionRequest
    policy_evaluator: Any
    approval_resolver: Any | None = None
    audit_append: Any
    audit_writer: Any | None = None


async def _append_audit(
    append: Any,
    writer: Any,
    record: AuditRecord,
) -> Any:
    if append is None:
        raise RuntimeError("audit append primitive is not configured")
    if writer is None:
        return await _call(append, record)
    return await _call(append, record, writer)


def _govern_record(request: ActionRequest, decision: ActionDecision) -> AuditRecord:
    return AuditRecord(
        event="action.governed",
        request_id=request.request_id,
        action=request.action,
        decision=decision.verdict,
        actor=request.actor,
        success=decision.verdict != "DENY",
        detail={
            "reason": decision.reason,
            "policy_id": decision.policy_id,
            "approved": decision.approved,
            "effect": request.effect,
        },
    )


async def govern_action(
    config: GovernActionConfig,
    input_data: GovernActionInput,
    output_dir: Path,
    *,
    on_step: Callable[[dict[str, Any]], Any] | None = None,
) -> dict[str, Any]:
    """Evaluate and, when needed, resolve approval without executing the action."""
    del config
    output_dir.mkdir(parents=True, exist_ok=True)
    trail = Trail()
    request = input_data.request
    trail.record(event="policy_evaluation", request_id=request.request_id)
    if on_step is not None:
        on_step({"event": "policy_evaluation", "request_id": request.request_id})

    try:
        raw_decision = await _call(input_data.policy_evaluator, request)
        decision = _decision(raw_decision, request.request_id)
    except Exception as exc:
        decision = ActionDecision(
            verdict="DENY",
            reason=f"policy evaluation failed: {type(exc).__name__}",
            request_id=request.request_id,
        )

    if decision.verdict == "REQUIRE_APPROVAL" and input_data.approval_resolver is not None:
        try:
            approved = bool(await _call(input_data.approval_resolver, request))
        except Exception:
            approved = False
        decision = ActionDecision(
            verdict="ALLOW" if approved else "DENY",
            reason="approval granted" if approved else "approval denied or unavailable",
            policy_id=decision.policy_id,
            approved=approved,
            request_id=request.request_id,
        )

    try:
        await _append_audit(
            input_data.audit_append,
            input_data.audit_writer,
            _govern_record(request, decision),
        )
    except Exception as exc:
        decision = ActionDecision(
            verdict="DENY",
            reason=f"audit append failed: {type(exc).__name__}",
            request_id=request.request_id,
        )
        trail.record(event="audit_failure", request_id=request.request_id)
        return build_result(
            status="failed",
            error={"type": "AuditAppendFailed", "message": str(exc)},
            trail=trail,
            decision=decision.to_dict(),
            executed=False,
        )

    trail.record(event="decision", verdict=decision.verdict)
    return build_result(
        status="completed" if decision.verdict == "ALLOW" else "failed",
        error=(
            None
            if decision.verdict == "ALLOW"
            else {"type": "ActionNotAuthorized", "message": decision.reason}
        ),
        trail=trail,
        decision=decision.to_dict(),
        executed=False,
    )


class ExecuteGovernedActionConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "execute_governed_action"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "decision_trail"}


class ExecuteGovernedActionInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    request: ActionRequest
    decision: ActionDecision
    executor: Any
    audit_append: Any
    audit_writer: Any | None = None
    side_effect_record: Any | None = None
    side_effect_recorder: Any | None = None
    operation_key: str = ""
    target_ref: str = ""
    capability: str = "manual_only"


def _execution_record(
    request: ActionRequest,
    decision: ActionDecision,
    *,
    success: bool,
    event: str,
    detail: Mapping[str, Any] | None = None,
) -> AuditRecord:
    return AuditRecord(
        event=event,
        request_id=request.request_id,
        action=request.action,
        decision=decision.verdict,
        actor=request.actor,
        success=success,
        detail={"effect": request.effect, **dict(detail or {})},
    )


async def execute_governed_action(
    config: ExecuteGovernedActionConfig,
    input_data: ExecuteGovernedActionInput,
    output_dir: Path,
    *,
    on_step: Callable[[dict[str, Any]], Any] | None = None,
) -> dict[str, Any]:
    """Execute an ALLOW decision exactly at the injected side-effect boundary."""
    del config
    output_dir.mkdir(parents=True, exist_ok=True)
    trail = Trail()
    request = input_data.request
    decision = input_data.decision
    fingerprint = compute_fingerprint(
        {
            "request": request.to_dict(),
            "operation_key": input_data.operation_key,
            "target_ref": input_data.target_ref,
        }
    )

    if decision.verdict != "ALLOW":
        return build_result(
            status="failed",
            error={"type": "ActionNotAuthorized", "message": decision.reason},
            fingerprint=fingerprint,
            trail=trail,
            decision=decision.to_dict(),
            executed=False,
        )

    try:
        await _append_audit(
            input_data.audit_append,
            input_data.audit_writer,
            _execution_record(request, decision, success=True, event="action.execute.authorized"),
        )
    except Exception as exc:
        return build_result(
            status="failed",
            error={"type": "AuditAppendFailed", "message": str(exc)},
            fingerprint=fingerprint,
            trail=trail,
            decision=decision.to_dict(),
            executed=False,
        )

    if on_step is not None:
        on_step({"event": "action_execute", "request_id": request.request_id})
    trail.record(event="action_execute", request_id=request.request_id)

    async def _invoke() -> Any:
        return await _call(tool_invoke, request, input_data.executor)

    try:
        if request.effect != "read":
            if input_data.side_effect_record is None or input_data.side_effect_recorder is None:
                raise RuntimeError("non-read action requires a side-effect recorder")
            result = await _call(
                input_data.side_effect_record,
                request=request,
                operation_key=input_data.operation_key or fingerprint,
                operation_type=request.effect,
                target_ref=input_data.target_ref or request.resource,
                provider=_invoke,
                recorder=input_data.side_effect_recorder,
                capability=input_data.capability,
            )
        else:
            result = await _invoke()
    except Exception as exc:
        with contextlib.suppress(Exception):
            await _append_audit(
                input_data.audit_append,
                input_data.audit_writer,
                _execution_record(
                    request,
                    decision,
                    success=False,
                    event="action.execute.failed",
                    detail={"error_type": type(exc).__name__},
                ),
            )
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
            fingerprint=fingerprint,
            trail=trail,
            decision=decision.to_dict(),
            executed=False,
        )

    try:
        await _append_audit(
            input_data.audit_append,
            input_data.audit_writer,
            _execution_record(request, decision, success=True, event="action.execute.completed"),
        )
    except Exception as exc:
        return build_result(
            status="failed",
            error={"type": "AuditAppendFailedAfterExecution", "message": str(exc)},
            fingerprint=fingerprint,
            trail=trail,
            decision=decision.to_dict(),
            executed=True,
            result=result,
        )
    return build_result(
        status="completed",
        error=None,
        fingerprint=fingerprint,
        trail=trail,
        decision=decision.to_dict(),
        executed=True,
        result=result,
    )


__all__ = [
    "ExecuteGovernedActionConfig",
    "ExecuteGovernedActionInput",
    "GovernActionConfig",
    "GovernActionInput",
    "execute_governed_action",
    "govern_action",
]
