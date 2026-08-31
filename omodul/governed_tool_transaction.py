"""Canonical governed native/MCP tool transaction.

This module composes the existing PR-09 Action Gateway.  It owns no policy,
approval store, audit sink, side-effect ledger, tool registry, or secret
storage.  Credentials are resolved only after the action decision is ALLOW.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, ClassVar

from obase.action import ActionDecision, ActionRequest, AuditRecord
from obase.tool_governance import (
    CredentialRef,
    SecretRef,
    ToolCallRequest,
    ToolSpec,
    redact_payload,
)
from pydantic import BaseModel, ConfigDict

from omodul._base import BaseConfig, Trail, build_result, compute_fingerprint


class GovernedToolConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "governed_tool_transaction"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "decision_trail"}


class GovernedToolInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    request: ToolCallRequest
    spec: ToolSpec | None = None
    registry: Any = None
    tool_resolve: Any = None
    prepare_tool_execution: Any = None
    govern_action: Any = None
    execute_governed_action: Any = None
    policy_evaluator: Any = None
    approval_resolver: Any = None
    audit_append: Any = None
    audit_writer: Any = None
    tool_call: Any = None
    mcp_call: Any = None
    executor: Any = None
    mcp_client: Any = None
    credential_resolve: Any = None
    secret_read: Any = None
    credential_resolver: Any = None
    side_effect_record: Any = None
    side_effect_recorder: Any = None
    operation_key: str = ""
    target_ref: str = ""
    capability: str = "manual_only"


async def _call(fn: Any, *args: Any, **kwargs: Any) -> Any:
    result = fn(*args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


def _credential_ref(request: ToolCallRequest, spec: ToolSpec) -> CredentialRef | SecretRef | None:
    return request.credential_ref or spec.credential_ref


def _decision(value: Any, request_id: str) -> ActionDecision:
    if isinstance(value, ActionDecision):
        return (
            value
            if value.request_id
            else ActionDecision(
                verdict=value.verdict,
                reason=value.reason,
                policy_id=value.policy_id,
                approved=value.approved,
                request_id=request_id,
            )
        )
    raw = value if isinstance(value, Mapping) else {}
    verdict = str(raw.get("verdict", "DENY")).upper()
    if verdict not in {"ALLOW", "DENY", "REQUIRE_APPROVAL"}:
        verdict = "DENY"
    return ActionDecision(
        verdict=verdict,  # type: ignore[arg-type]
        reason=str(raw.get("reason", "")),
        policy_id=raw.get("policy_id"),
        approved=bool(raw.get("approved", False)),
        request_id=request_id,
    )


async def _audit(append: Any, writer: Any, record: AuditRecord) -> None:
    if append is None or writer is None:
        return
    await _call(append, record, writer)


def _audit_record(
    request: ToolCallRequest,
    event: str,
    *,
    decision: str,
    detail: Mapping[str, Any] | None = None,
) -> AuditRecord:
    return AuditRecord(
        event=event,
        request_id=request.request_id,
        action=request.identity,
        decision=decision,  # type: ignore[arg-type]
        actor=request.actor,
        success=decision != "DENY",
        detail=redact_payload(detail or {}),
    )


def _safe_error(exc: BaseException, *, message: str = "tool transaction failed") -> dict[str, str]:
    """Never return exception text: transports may echo credentials."""
    return {"type": type(exc).__name__, "message": message}


async def _invoke_physical(executor: Any, request: ActionRequest, secret: str | None) -> Any:
    if executor is None:
        raise RuntimeError("tool executor is not configured")
    if secret is None:
        return await _call(executor, request)
    try:
        signature = inspect.signature(executor)
    except (TypeError, ValueError):
        return await _call(executor, request, secret)
    names = signature.parameters
    if "_injected_secret" in names:
        return await _call(executor, request, _injected_secret=secret)
    if "credential" in names:
        return await _call(executor, request, credential=secret)
    if "secret" in names:
        return await _call(executor, request, secret=secret)
    accepts_varargs = any(p.kind is inspect.Parameter.VAR_POSITIONAL for p in names.values())
    positional = [
        p
        for p in names.values()
        if p.kind in {inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD}
    ]
    if accepts_varargs or len(positional) >= 2:
        return await _call(executor, request, secret)
    raise RuntimeError("credential-bound executor does not accept a credential parameter")


async def governed_tool_transaction(
    config: GovernedToolConfig,
    input_data: GovernedToolInput,
    output_dir: Path,
    *,
    on_step: Callable[[dict[str, Any]], Any] | None = None,
) -> dict[str, Any]:
    """Authorize and execute one native or MCP tool call through Action Gateway."""
    del config
    output_dir.mkdir(parents=True, exist_ok=True)
    trail = Trail()
    request = input_data.request
    fingerprint = compute_fingerprint(
        {"request": request.to_dict(), "operation_key": input_data.operation_key}
    )

    try:
        spec = input_data.spec
        if spec is None:
            if input_data.tool_resolve is None or input_data.registry is None:
                raise RuntimeError("tool contract is not configured")
            spec = await _call(
                input_data.tool_resolve,
                request.identity,
                registry=input_data.registry,
            )
        if input_data.prepare_tool_execution is None:
            raise RuntimeError("tool grant preparation is not configured")
        prepared = await _call(
            input_data.prepare_tool_execution,
            spec,
            request.grant,
            actor=request.actor,
            resource=input_data.target_ref or request.identity,
            arguments=request.arguments,
        )
        if str(prepared.get("verdict", "DENY")) != "ALLOW":
            trail.record(event="grant_denied", tool=request.identity)
            record = _audit_record(
                request,
                "tool.grant_denied",
                decision="DENY",
                detail={"reason": prepared.get("reason", "grant denied")},
            )
            try:
                await _audit(input_data.audit_append, input_data.audit_writer, record)
            except Exception:
                return build_result(
                    status="failed",
                    error={"type": "AuditAppendFailed", "message": "audit unavailable"},
                    fingerprint=fingerprint,
                    trail=trail,
                    executed=False,
                )
            return build_result(
                status="failed",
                error={"type": "GrantDenied", "message": "tool grant denied"},
                fingerprint=fingerprint,
                trail=trail,
                decision={"verdict": "DENY", "reason": prepared.get("reason", "grant denied")},
                executed=False,
            )

        effect = str(prepared.get("effect", spec.effect))
        ref = _credential_ref(request, spec)
        action_request = ActionRequest(
            action=request.tool,
            effect=effect,  # type: ignore[arg-type]
            resource=input_data.target_ref or request.identity,
            arguments=request.arguments,
            actor=request.actor,
            request_id=request.request_id,
            source=request.source,
            context={
                **dict(request.context),
                "tool_identity": request.identity,
                "grant_id": request.grant.grant_id if request.grant else None,
                "credential_ref": ref.to_dict() if ref else None,
                "side_effect_declared": True,
            },
        )
        if on_step is not None:
            on_step({"event": "tool_governance", "tool": request.identity})
        trail.record(event="action_gateway", tool=request.identity)

        from omodul.action_gateway import (
            ExecuteGovernedActionConfig,
            ExecuteGovernedActionInput,
            GovernActionConfig,
            GovernActionInput,
        )
        from omodul.action_gateway import (
            execute_governed_action as canonical_execute,
        )
        from omodul.action_gateway import (
            govern_action as canonical_govern,
        )

        govern = input_data.govern_action or canonical_govern
        execute = input_data.execute_governed_action or canonical_execute
        policy = input_data.policy_evaluator
        if policy is None:
            from oskill.action_governance import evaluate_action_policy

            policy = evaluate_action_policy
        if input_data.audit_append is None or input_data.audit_writer is None:
            raise RuntimeError("Action Gateway audit injection is incomplete")
        governed = await _call(
            govern,
            GovernActionConfig(),
            GovernActionInput(
                request=action_request,
                policy_evaluator=policy,
                approval_resolver=input_data.approval_resolver,
                audit_append=input_data.audit_append,
                audit_writer=input_data.audit_writer,
            ),
            output_dir,
            on_step=on_step,
        )
        decision = _decision(governed.get("decision"), request.request_id)
        if decision.verdict != "ALLOW":
            return build_result(
                status="failed",
                error={"type": "ActionNotAuthorized", "message": "tool action not authorized"},
                fingerprint=fingerprint,
                trail=trail,
                decision=decision.to_dict(),
                executed=False,
            )

        # This is after policy/approval and before the physical call.  The raw
        # value lives only in this coroutine/physical callback.
        secret: str | None = None
        if ref is not None:
            resolver_primitive = (
                input_data.secret_read
                if isinstance(ref, SecretRef) and input_data.secret_read is not None
                else input_data.credential_resolve
            )
            if resolver_primitive is None or input_data.credential_resolver is None:
                raise RuntimeError("credential resolver is not configured")
            if resolver_primitive is input_data.secret_read:
                secret = await _call(
                    resolver_primitive,
                    ref,
                    reader=input_data.credential_resolver,
                )
            else:
                secret = await _call(
                    resolver_primitive,
                    ref,
                    resolver=input_data.credential_resolver,
                )

        async def physical(_action: ActionRequest) -> Any:
            try:
                if request.kind == "mcp":
                    if input_data.mcp_call is None or input_data.mcp_client is None:
                        raise RuntimeError("MCP call injection is incomplete")
                    raw = await _call(
                        input_data.mcp_call,
                        request.tool,
                        arguments=dict(request.arguments),
                        client=input_data.mcp_client,
                        server=request.server or "",
                        credential=secret,
                    )
                else:
                    if input_data.tool_call is None:
                        raise RuntimeError("native tool call injection is incomplete")
                    raw = await _call(
                        input_data.tool_call,
                        request,
                        executor=lambda _request: _invoke_physical(
                            input_data.executor, _action, secret
                        ),
                    )
                # Redact before Action Gateway/SideEffectLedger can persist a
                # result, not only when returning to Layer 4.
                return redact_payload(raw, secrets=(secret,) if secret else ())
            except Exception as exc:
                raise RuntimeError("physical tool execution failed") from exc

        executed = await _call(
            execute,
            ExecuteGovernedActionConfig(),
            ExecuteGovernedActionInput(
                request=action_request,
                decision=decision,
                executor=physical,
                audit_append=input_data.audit_append,
                audit_writer=input_data.audit_writer,
                side_effect_record=input_data.side_effect_record,
                side_effect_recorder=input_data.side_effect_recorder,
                operation_key=input_data.operation_key or fingerprint,
                target_ref=input_data.target_ref or request.identity,
                capability=input_data.capability,
            ),
            output_dir,
            on_step=on_step,
        )
        if secret:
            executed = redact_payload(executed, secrets=(secret,))
        if executed.get("status") != "completed":
            return build_result(
                status="failed",
                error=_safe_error(RuntimeError("physical tool failed")),
                fingerprint=fingerprint,
                trail=trail,
                decision=decision.to_dict(),
                executed=bool(executed.get("executed", False)),
                result=redact_payload(executed.get("result"), secrets=(secret,) if secret else ()),
            )
        return build_result(
            status="completed",
            error=None,
            fingerprint=fingerprint,
            trail=trail,
            decision=decision.to_dict(),
            executed=True,
            result=redact_payload(executed.get("result"), secrets=(secret,) if secret else ()),
            tool=request.identity,
            credential_ref=ref.to_dict() if ref else None,
        )
    except Exception as exc:
        trail.record(event="tool_transaction_failed", tool=request.identity)
        return build_result(
            status="failed",
            error=_safe_error(exc),
            fingerprint=fingerprint,
            trail=trail,
            executed=False,
        )


__all__ = [
    "GovernedToolConfig",
    "GovernedToolInput",
    "governed_tool_transaction",
]
