"""Contract tests for attempted-vs-completed governed tool execution."""

from __future__ import annotations

from pathlib import Path

import pytest
from obase.tool_governance import ToolCallRequest, ToolSpec

from omodul.governed_tool_transaction import (
    GovernedToolConfig,
    GovernedToolInput,
    governed_tool_transaction,
)


def _request(code: str) -> ToolCallRequest:
    return ToolCallRequest(
        tool="run_in_sandbox",
        kind="native",
        version="1",
        arguments={"code": code},
        actor="master",
        request_id=f"request-{hash(code)}",
    )


def _spec() -> ToolSpec:
    return ToolSpec(
        name="run_in_sandbox",
        kind="native",
        effect="process",
        input_schema={"type": "object"},
    )


async def _run(code: str, *, outcome: str = "success") -> dict:
    async def allow(*args, **kwargs):
        return {"decision": {"verdict": "ALLOW", "reason": "test"}}

    async def prepare(*args, **kwargs):
        return {"verdict": "ALLOW", "effect": "process"}

    async def tool_call(request, *, executor):
        return await executor(request)

    async def physical_executor(request):
        if outcome == "failure":
            raise ValueError("sanitized physical failure")
        return "physical-result"

    async def execute(config, data, output_dir, *, on_step=None):
        try:
            result = await data.executor(data.request)
        except Exception as exc:
            return {
                "status": "failed",
                "executed": False,
                "error": {"type": type(exc).__name__, "message": str(exc)},
            }
        return {"status": "completed", "executed": True, "result": result}

    data = GovernedToolInput(
        request=_request(code),
        spec=_spec(),
        prepare_tool_execution=prepare,
        govern_action=allow,
        execute_governed_action=execute,
        policy_evaluator=allow,
        approval_resolver=allow,
        audit_append=lambda *args, **kwargs: None,
        audit_writer=lambda *args, **kwargs: None,
        tool_call=tool_call,
        executor=physical_executor,
        operation_key=f"operation:{hash(code)}",
        target_ref="native/run_in_sandbox@1",
    )
    return await governed_tool_transaction(GovernedToolConfig(), data, Path(".pytest-tmp"))


@pytest.mark.asyncio
async def test_governance_denial_is_not_attempted():
    async def deny(*args, **kwargs):
        return {"decision": {"verdict": "DENY", "reason": "policy"}}

    async def prepare(*args, **kwargs):
        return {"verdict": "ALLOW", "effect": "process"}

    data = GovernedToolInput(
        request=_request("denied"),
        spec=_spec(),
        prepare_tool_execution=prepare,
        govern_action=deny,
        audit_append=lambda *args, **kwargs: None,
        audit_writer=lambda *args, **kwargs: None,
        operation_key="operation:denied",
        target_ref="native/run_in_sandbox@1",
    )
    result = await governed_tool_transaction(GovernedToolConfig(), data, Path(".pytest-tmp"))
    assert result["status"] == "failed"
    assert result["attempted"] is False
    assert result["executed"] is False


@pytest.mark.asyncio
async def test_physical_failure_is_attempted_but_not_executed():
    result = await _run("raise", outcome="failure")
    assert result["status"] == "failed"
    assert result["executed"] is False
    assert result["attempted"] is True
    assert result["failure_stage"] == "physical_execution"
    assert result["physical_error_type"] == "ValueError"
    assert result["physical_error_message"] == "sanitized physical failure"


@pytest.mark.asyncio
async def test_physical_success_is_attempted_and_completed():
    result = await _run("ok")
    assert result["status"] == "completed"
    assert result["attempted"] is True
    assert result["executed"] is True


@pytest.mark.asyncio
async def test_distinct_physical_actions_have_distinct_transaction_identity():
    first = await _run("first", outcome="failure")
    second = await _run("second")
    assert first["fingerprint"] != second["fingerprint"]
