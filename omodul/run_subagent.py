"""Auto-split from hicode whl."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Callable
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Literal

from oprim import bash_exec, file_read, file_write, run_hook
from oskill import (
    build_subagent_prompt as _build_prompt,
)
from oskill import (
    evaluate_hooks as _evaluate_hooks,
)
from oskill import (
    match_permission_rule as _match_permission_rule,
)
from oskill import (
    merge_subagent_result as _merge_result,
)
from oskill import (
    parse_llm_tool_calls as _parse_tool_calls,
)
from pydantic import BaseModel, Field

RECURSION_DEPTH_LIMIT = 5


# Runtime-local coordination state.  These ContextVars are intentionally
# private implementation state, but are exported by ``omodul`` for the
# existing test/integration contract.
_current_cost: ContextVar[CostTracker | None]  # declared after CostTracker
_current_depth: ContextVar[int]
_current_trail: ContextVar[list[dict]]


class LLMCaller:
    """obase.LLMCaller Protocol 占位（生产从 obase.ProviderRegistry 取实例）。"""

    async def __call__(
        self,
        *,
        messages: list[dict],
        tools: list[dict] | None = None,
        max_tokens: int = 4096,
        thinking_budget: int | None = None,
    ) -> dict:
        raise NotImplementedError


@dataclass
class CostTracker:
    """obase.CostTracker 占位。并发安全：只在对象上累加，不替换引用。"""

    total_usd: float = 0.0
    in_tokens: int = 0
    out_tokens: int = 0

    def add(self, *, in_tok: int, out_tok: int, model: str, pricing: dict) -> float:
        cost = in_tok * pricing.get("in", 3e-06) + out_tok * pricing.get("out", 1.5e-05)
        self.total_usd += cost
        self.in_tokens += in_tok
        self.out_tokens += out_tok
        return cost


_current_cost = ContextVar("omodul_current_cost", default=None)
_current_depth = ContextVar("omodul_current_depth", default=0)
_current_trail = ContextVar("omodul_current_trail", default=[])


@dataclass
class HookSpec:
    event: str
    command: str
    matcher: str | None = None


@dataclass
class SubagentPermissions:
    """per-subagent 工具权限策略。对齐 obase.permissions 四作用域。"""

    allowed_tools: list[str] = field(default_factory=list)
    denied_tools: list[str] = field(default_factory=list)
    mode: Literal["default", "acceptEdits", "plan", "bypass"] = "default"
    max_bash_timeout: int = 120
    allow_network: bool = True
    allow_file_write: bool = True


@dataclass
class SubagentDefinition:
    """
    对应 .claude/agents/<name>.md 解析结果。
    生产环境由 obase.skills / Layer 4 subagent-loader 产出。
    """

    name: str
    system_prompt: str
    tools: list[dict]
    permissions: SubagentPermissions = field(default_factory=SubagentPermissions)
    memory_dir: Path | None = None
    hook_specs: list[HookSpec] = field(default_factory=list)
    thinking_budget: int | None = None


class SubagentConfig(BaseModel):
    """omodul BaseConfig（§5.3 标准）。"""

    llm_provider: str = "anthropic"
    llm_model: str = "claude-sonnet-4-6"
    budget_usd: float = 2.0
    max_iterations: int = 20
    thinking_budget: int | None = None
    _omodul_name: ClassVar[str] = "run_subagent"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = set()
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail", "cost"}


class SubagentInput(BaseModel):
    task: str
    subagent_def: SubagentDefinition
    context: str = ""
    caller: Any = None
    pricing: dict = Field(default_factory=lambda: {"in": 3e-06, "out": 1.5e-05})
    global_hook_specs: list[HookSpec] = Field(default_factory=list)


def _build_subagent_prompt(
    defn: SubagentDefinition,
    task: str,
    context: str,
) -> dict[str, Any]:
    permissions = {
        "allowed_tools": defn.permissions.allowed_tools,
        "denied_tools": defn.permissions.denied_tools,
        "mode": defn.permissions.mode,
    }
    return _build_prompt(
        {
            "system_prompt": defn.system_prompt,
            "tools": defn.tools,
            "permissions": permissions,
        },
        task,
        context=context,
    )


def _record_step(trail: list[dict], *, step_no: int, event: str, **details) -> None:
    trail.append({"step_no": step_no, "event": event, **details})


async def _write_trail(trail: list[dict], output_dir: Path, run_id: str) -> Path:
    path = output_dir / f"decision_trail_{run_id}.json"
    content = json.dumps(trail, ensure_ascii=False, indent=2)
    await asyncio.to_thread(path.write_text, content, encoding="utf-8")
    return path


async def _run_tool(name: str, tool_input: dict, permissions: SubagentPermissions) -> str:
    if name == "bash_exec":
        command = tool_input.get("command", tool_input.get("cmd", ""))
        result = await asyncio.to_thread(
            bash_exec,
            command,
            timeout=permissions.max_bash_timeout,
        )
        return result.stdout if result.ok else result.stderr or result.stdout
    if name == "file_read":
        return await asyncio.to_thread(file_read, tool_input.get("path", ""))
    if name == "file_write":
        path = await asyncio.to_thread(
            file_write,
            tool_input.get("path", ""),
            content=tool_input.get("content", ""),
        )
        return f"written: {path}"
    return f"tool_not_found: {name}"


async def _run_agentic_loop(
    *,
    system: str,
    task: str,
    scoped_tools: list[dict],
    caller: Any,
    permissions: SubagentPermissions,
    hook_specs: list[HookSpec],
    config: SubagentConfig,
    trail: list[dict],
    cost_tracker: CostTracker,
    pricing: dict,
) -> list[dict]:
    messages: list[dict] = [
        {"role": "system", "content": system},
        {"role": "user", "content": task},
    ]
    results: list[dict] = []
    tool_names = {tool.get("name", "") for tool in scoped_tools}

    for iteration in range(config.max_iterations):
        response = await caller(
            messages=messages,
            tools=scoped_tools,
            max_tokens=4096,
            thinking_budget=config.thinking_budget,
        )
        usage = response.get("usage", {})
        in_tokens = usage.get("input_tokens") or usage.get("prompt_tokens") or 0
        out_tokens = usage.get("output_tokens") or usage.get("completion_tokens") or 0
        call_cost = cost_tracker.add(
            in_tok=in_tokens,
            out_tok=out_tokens,
            model=config.llm_model,
            pricing=pricing,
        )
        _record_step(
            trail,
            step_no=len(trail) + 1,
            event="llm_call",
            iteration=iteration + 1,
            cost_usd=round(call_cost, 6),
        )
        content = response.get("content", [])
        if isinstance(content, str):
            text = content
        else:
            text = "".join(
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            )
        results.append(
            {
                "subagent_name": "subagent",
                "summary": text,
                "status": "completed",
                "iterations": iteration + 1,
                "cost_usd": call_cost,
            }
        )

        tool_calls = _parse_tool_calls(response)
        if not tool_calls:
            break

        messages.append({"role": "assistant", "content": content})
        tool_results = []
        for call in tool_calls:
            decision = _match_permission_rule(
                {"name": call.name},
                allowed_tools=permissions.allowed_tools,
                denied_tools=permissions.denied_tools,
                mode=permissions.mode,
            )
            if decision == "ask" and call.name in tool_names:
                decision = "allow"
            if call.name == "file_write" and not permissions.allow_file_write:
                decision = "deny"
            if decision != "allow":
                _record_step(
                    trail,
                    step_no=len(trail) + 1,
                    event="permission_denied",
                    tool=call.name,
                )
                result_text = f"permission_denied: {call.name}"
            else:
                hook_payload = {"event": "PreToolUse", "tool": call.name, "input": call.input}
                hook_blocked = False
                for hook in _evaluate_hooks(
                    "PreToolUse",
                    hook_payload,
                    hook_specs=[
                        {
                            "event": item.event,
                            "command": item.command,
                            "matcher": item.matcher,
                        }
                        for item in hook_specs
                    ],
                ):
                    hook_result = await run_hook(
                        hook.command,
                        event_json=hook_payload,
                        timeout=permissions.max_bash_timeout,
                    )
                    if hook_result.decision == "block":
                        hook_blocked = True
                        _record_step(
                            trail,
                            step_no=len(trail) + 1,
                            event="hook_blocked",
                            tool=call.name,
                        )
                        result_text = hook_result.output or "blocked by hook"
                        break
                if not hook_blocked:
                    _record_step(
                        trail,
                        step_no=len(trail) + 1,
                        event="tool_call",
                        tool=call.name,
                    )
                    result_text = await _run_tool(call.name, call.input, permissions)
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": call.id,
                    "content": result_text,
                }
            )
        messages.append({"role": "user", "content": tool_results})

    return results


def _merge_subagent_result(step_results: list[dict], task: str) -> str:
    return _merge_result(step_results, task=task)


async def run_subagent(
    config: SubagentConfig,
    input_data: SubagentInput,
    output_dir: Path,
    *,
    on_step: Callable[[dict], None] | None = None,
) -> dict:
    """
    omodul: run_subagent
    =====================
    有界子 agent 调用事务。

    归属约束检查单（§5 SPEC v2.1）
    ✅ 标准签名 (config, input_data, output_dir) -> dict
    ✅ status / error 必返回
    ✅ 失败不 raise（CancelledError 除外）
    ✅ _enabled_pillars = {"decision_trail", "cost"}（显式，≥1）
    ✅ 内部组合 ≥2 oskill/oprim
    ✅ async 本性（重 IO：多次 LLM 调用 + hook subprocess）
    ✅ 递归深度守卫（≤5 层）
    ✅ ContextVar 铁律（共享对象引用累加，不 .set() 新对象）
    ✅ CancelledError 重抛；trail 落盘用 asyncio.shield

    返回结构
    --------
    {
        "summary": str,           # 子 agent 产出摘要（返回主 agent）
        "status": "completed" | "failed" | "cancelled" | "depth_exceeded" | "budget_exceeded",
        "error": dict | None,
        "decision_trail": dict,   # 落盘路径 + 步骤数
        "cost_usd": float,        # 本次子 agent 消耗
        "iterations": int,
        "subagent_name": str,
        "depth": int,             # 本层递归深度
    }
    """
    run_id = str(uuid.uuid4())[:8]
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    defn = input_data.subagent_def

    # ── 递归深度守卫 ──────────────────────────────────────────────────────
    # 每层 run_subagent 从 ContextVar 取当前深度，+1 写入本层隔离副本
    current_depth = _current_depth.get()
    depth_token: Token = _current_depth.set(current_depth + 1)
    my_depth = current_depth + 1

    if current_depth >= RECURSION_DEPTH_LIMIT:
        return {
            "summary": "",
            "status": "depth_exceeded",
            "error": {
                "type": "RecursionDepthExceeded",
                "limit": RECURSION_DEPTH_LIMIT,
                "current": current_depth,
                "subagent": defn.name,
            },
            "decision_trail": None,
            "cost_usd": 0.0,
            "iterations": 0,
            "subagent_name": defn.name,
            "depth": my_depth,
        }

    # ── ContextVar：cost / trail ──────────────────────────────────────────
    # 优先复用父层 CostTracker（跨层累加），若顶层则新建
    parent_cost = _current_cost.get()
    if parent_cost is None:
        cost_tracker = CostTracker()
        cost_token: Token = _current_cost.set(cost_tracker)
    else:
        cost_tracker = parent_cost  # 共享父层对象引用（铁律）  # pragma: no cover
        cost_token = None  # pragma: no cover

    # trail 每个子 agent 独立（隔离语义），不与父共享
    trail: list[dict] = []
    trail_token: Token = _current_trail.set(trail)

    status = "completed"
    error: dict | None = None
    summary = ""
    iterations = 0

    try:
        # ── oskill: build_subagent_prompt ─────────────────────────────────
        prompt_ctx = _build_subagent_prompt(defn, input_data.task, input_data.context)
        system = prompt_ctx["system"]
        scoped_tools = prompt_ctx["scoped_tools"]

        _record_step(
            trail,
            step_no=0,
            event="subagent_start",
            name=defn.name,
            depth=my_depth,
            task_preview=input_data.task[:200],
        )

        if on_step:
            on_step({"event": "subagent_start", "name": defn.name, "depth": my_depth})

        # ── 合并 hook specs（frontmatter + global）────────────────────────
        merged_hooks = defn.hook_specs + input_data.global_hook_specs

        # ── 内嵌有界 agentic loop ─────────────────────────────────────────
        step_results = await _run_agentic_loop(
            system=system,
            task=input_data.task,
            scoped_tools=scoped_tools,
            caller=input_data.caller,
            permissions=defn.permissions,
            hook_specs=merged_hooks,
            config=config,
            trail=trail,
            cost_tracker=cost_tracker,
            pricing=input_data.pricing,
        )

        # budget 检查（loop 可能因 budget 中断）
        if cost_tracker.total_usd > config.budget_usd:
            status = "budget_exceeded"
            error = {
                "type": "BudgetExceeded",
                "spent_usd": round(cost_tracker.total_usd, 6),
                "limit_usd": config.budget_usd,
            }

        # ── oskill: merge_subagent_result ─────────────────────────────────
        summary = _merge_subagent_result(step_results, input_data.task)

        iterations = sum(1 for s in trail if s.get("event") == "llm_call")

        _record_step(
            trail,
            step_no=len(trail) + 1,
            event="subagent_complete",
            status=status,
            summary_len=len(summary),
            cost_usd=round(cost_tracker.total_usd, 6),
        )

        if on_step:
            on_step(
                {
                    "event": "subagent_complete",
                    "name": defn.name,
                    "status": status,
                    "iterations": iterations,
                }
            )

    except asyncio.CancelledError:
        # ── CancelledError：重抛，但先保护 trail 落盘 ───────────────────
        status = "cancelled"  # pragma: no cover
        error = {"type": "Cancelled", "reason": "task cancelled or timeout"}  # pragma: no cover
        _record_step(trail, step_no=len(trail) + 1, event="subagent_cancelled")  # pragma: no cover
        # asyncio.shield 保护落盘不被取消打断（§5.6 C4 铁律）
        await asyncio.shield(  # pragma: no cover
            _write_trail(trail, output_dir, run_id)
        )
        raise  # 必须重抛，不可吞  # pragma: no cover

    except Exception as exc:
        status = "failed"
        error = {
            "type": type(exc).__name__,
            "message": str(exc),
            "subagent": defn.name,
            "depth": my_depth,
        }
        _record_step(trail, step_no=len(trail) + 1, event="subagent_error", error=error)
        if on_step:
            on_step({"event": "subagent_error", "error": error})  # pragma: no cover
        # 失败不 raise（§5.4 MUST）；status="failed" 返回

    finally:
        # ── decision_trail 落盘（不被 CancelledError 以外的异常跳过）─────
        if status != "cancelled":  # cancelled 已在上方 shield 落盘
            try:
                await _write_trail(trail, output_dir, run_id)
            except Exception:  # pragma: no cover
                pass  # 落盘失败不掩盖主错误  # pragma: no cover

        # ── 还原 ContextVar（退出本层隔离）──────────────────────────────
        _current_trail.reset(trail_token)
        _current_depth.reset(depth_token)
        if cost_token is not None:
            _current_cost.reset(cost_token)

    trail_path = output_dir / f"decision_trail_{run_id}.json"

    return {
        "summary": summary,
        "status": status,
        "error": error,
        "decision_trail": {
            "path": str(trail_path),
            "steps": len(trail),
            "run_id": run_id,
        },
        "cost_usd": round(cost_tracker.total_usd, 6),
        "iterations": iterations,
        "subagent_name": defn.name,
        "depth": my_depth,
    }
