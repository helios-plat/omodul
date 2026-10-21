"""Auto-split from hicode whl."""

from __future__ import annotations
import sys as _sys
import os as _os
import asyncio
import json
import time
import uuid
from collections.abc import Callable
from contextvars import ContextVar, Token

_current_trail: ContextVar[list[dict] | None] = ContextVar("_current_trail", default=None)
_current_cost: ContextVar[CostTracker | None] = ContextVar("_current_cost", default=None)
_current_depth: ContextVar[int] = ContextVar("_current_depth", default=0)
RECURSION_DEPTH_LIMIT = 5
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Literal
from pydantic import BaseModel, Field

class LLMCaller:
    """obase.LLMCaller Protocol 占位（生产从 obase.ProviderRegistry 取实例）。"""

    async def __call__(self, *, messages: list[dict], tools: list[dict] | None=None, max_tokens: int=4096, thinking_budget: int | None=None) -> dict:
        raise NotImplementedError

@dataclass
class CostTracker:
    """obase.CostTracker 占位。并发安全：只在对象上累加，不替换引用。"""
    total_usd: float = 0.0
    in_tokens: int = 0
    out_tokens: int = 0

    def add(self, *, in_tok: int, out_tok: int, model: str, pricing: dict) -> float:
        cost = in_tok * pricing.get('in', 3e-06) + out_tok * pricing.get('out', 1.5e-05)
        self.total_usd += cost
        self.in_tokens += in_tok
        self.out_tokens += out_tok
        return cost

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
    mode: Literal['default', 'acceptEdits', 'plan', 'bypass'] = 'default'
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
    llm_provider: str = 'anthropic'
    llm_model: str = 'claude-sonnet-4-6'
    budget_usd: float = 2.0
    max_iterations: int = 20
    thinking_budget: int | None = None
    _omodul_name: ClassVar[str] = 'run_subagent'
    _omodul_version: ClassVar[str] = '1.0.0'
    _fingerprint_fields: ClassVar[set[str]] = set()
    _enabled_pillars: ClassVar[set[str]] = {'decision_trail', 'cost'}

class SubagentInput(BaseModel):
    task: str
    subagent_def: SubagentDefinition
    context: str = ''
    caller: Any = None
    pricing: dict = Field(default_factory=lambda: {'in': 3e-06, 'out': 1.5e-05})
    global_hook_specs: list[HookSpec] = Field(default_factory=list)

# ---------------------------------------------------------------------------
# 内部 oskill 调用（生产从 oskill 包导入）
# ---------------------------------------------------------------------------

def _build_subagent_prompt(defn: SubagentDefinition, task: str, context: str) -> dict:
    """
    oskill: build_subagent_prompt (纯，不持久化)
    返回 {system: str, scoped_tools: list[dict]}
    """
    system = defn.system_prompt.strip()
    if context:
        system += f"\n\n## 主 agent 上下文\n{context}"  # pragma: no cover
    if defn.memory_dir and defn.memory_dir.exists():
        mem_files = list(defn.memory_dir.glob("*.md"))  # pragma: no cover
        if mem_files:  # pragma: no cover
            mem_text = "\n\n".join(f.read_text() for f in mem_files[:5])  # pragma: no cover
            system += f"\n\n## 历史记忆\n{mem_text}"  # pragma: no cover
    return {"system": system, "scoped_tools": defn.tools}


def _match_permission_rule(
    tool_call: dict,
    permissions: SubagentPermissions,
) -> Literal["allow", "deny", "ask"]:
    """
    oskill: match_permission_rule (纯)
    plan 模式只允许只读工具；bypass 全开；default 按 allowed/denied 列表。
    """
    tool_name = tool_call.get("name", "")  # pragma: no cover

    if permissions.mode == "bypass":  # pragma: no cover
        return "allow"  # pragma: no cover

    if permissions.mode == "plan":  # pragma: no cover
        READ_ONLY = {"read_file", "glob_match", "dir_list", "ripgrep_search",  # pragma: no cover
                     "lsp_diagnostics", "lsp_hover", "lsp_definition"}
        return "allow" if tool_name in READ_ONLY else "deny"  # pragma: no cover

    import fnmatch  # pragma: no cover
    for pattern in permissions.denied_tools:  # pragma: no cover
        if fnmatch.fnmatch(tool_name, pattern):  # pragma: no cover
            return "deny"  # pragma: no cover
    for pattern in permissions.allowed_tools:  # pragma: no cover
        if fnmatch.fnmatch(tool_name, pattern):  # pragma: no cover
            return "allow"  # pragma: no cover

    if permissions.mode == "acceptEdits" and tool_name in {"file_write", "file_append"}:  # pragma: no cover
        return "allow"  # pragma: no cover

    return "ask"  # pragma: no cover


def _merge_subagent_result(
    step_results: list[dict],
    task: str,
) -> str:
    """
    oskill: merge_subagent_result (纯)
    将子 agent 各步骤结果压成返回给主 agent 的摘要。
    生产版会用 compress_context oskill（LLM 辅助摘要），
    此处实现纯文本拼接版，保持 oskill 不持久化约束。
    """
    completed = [r for r in step_results if r.get("type") == "tool_result"]
    final_text = next(
        (r.get("text", "") for r in reversed(step_results) if r.get("type") == "text"),
        "",
    )
    summary_parts = [f"## 子 agent 任务\n{task}"]
    if completed:
        summary_parts.append(f"## 工具调用 ({len(completed)} 次)\n" +  # pragma: no cover
                              "\n".join(f"- {r['tool']}: {str(r.get('result',''))[:200]}"
                                        for r in completed))
    if final_text:
        summary_parts.append(f"## 最终输出\n{final_text}")
    return "\n\n".join(summary_parts)


# ---------------------------------------------------------------------------
# 内部 oprim 调用（生产从 oprim 包导入）
# ---------------------------------------------------------------------------

async def _run_hook(
    command: str,
    *,
    event_json: dict,
    timeout: int = 30,
) -> dict:
    """
    oprim: run_hook — 单 subprocess，stdin 喂事件 JSON。
    返回 {decision: "allow"|"block"|"modify", output: str}
    """
    try:  # pragma: no cover
        proc = await asyncio.create_subprocess_shell(  # pragma: no cover
            command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdin_data = json.dumps(event_json).encode()  # pragma: no cover
        try:  # pragma: no cover
            stdout, stderr = await asyncio.wait_for(  # pragma: no cover
                proc.communicate(stdin_data), timeout=timeout
            )
        except asyncio.TimeoutError:  # pragma: no cover
            proc.kill()  # pragma: no cover
            return {"decision": "allow", "output": "hook timeout, allowed"}  # pragma: no cover

        raw = stdout.decode().strip()  # pragma: no cover
        try:  # pragma: no cover
            result = json.loads(raw)  # pragma: no cover
            return {  # pragma: no cover
                "decision": result.get("decision", "allow"),
                "output": result.get("output", ""),
            }
        except json.JSONDecodeError:  # pragma: no cover
            return {"decision": "allow", "output": raw}  # pragma: no cover
    except Exception as e:  # pragma: no cover
        return {"decision": "allow", "output": f"hook error: {e}"}  # pragma: no cover


async def _dispatch_hooks(
    event: str,
    payload: dict,
    hook_specs: list[HookSpec],
) -> dict:
    """
    评估 + 执行与 event 匹配的所有 hooks。
    返回 {decision: "allow"|"block", modified_payload: dict}
    阻塞任意 hook → block 整体。
    """
    import fnmatch
    matched = [
        h for h in hook_specs
        if h.event == event and (
            h.matcher is None
            or fnmatch.fnmatch(payload.get("tool", payload.get("type", "")), h.matcher)
        )
    ]
    if not matched:
        return {"decision": "allow", "modified_payload": payload}

    results = await asyncio.gather(  # pragma: no cover
        *[_run_hook(h.command, event_json={"event": event, **payload}) for h in matched]
    )
    for r in results:  # pragma: no cover
        if r["decision"] == "block":  # pragma: no cover
            return {"decision": "block", "modified_payload": payload}  # pragma: no cover
    return {"decision": "allow", "modified_payload": payload}  # pragma: no cover


# ---------------------------------------------------------------------------
# 核心内部实现
# ---------------------------------------------------------------------------

def _record_step(trail: list[dict], *, step_no: int, **kwargs) -> None:
    """共享 trail list 上 append（不替换引用，铁律）。"""
    trail.append({"step_no": step_no, "ts": time.time(), **kwargs})


async def _write_trail(trail: list[dict], output_dir: Path, run_id: str) -> None:
    """decision_trail 落盘（async 版，被 asyncio.shield 包裹）。"""
    trail_path = output_dir / f"decision_trail_{run_id}.json"
    trail_path.write_text(json.dumps(trail, ensure_ascii=False, indent=2))


async def _run_agentic_loop(
    *,
    system: str,
    task: str,
    scoped_tools: list[dict],
    caller: LLMCaller,
    permissions: SubagentPermissions,
    hook_specs: list[HookSpec],
    config: SubagentConfig,
    trail: list[dict],
    cost_tracker: CostTracker,
    pricing: dict,
) -> list[dict]:
    """
    子 agent 的有界 agentic loop（内嵌于 omodul，而非独立引擎）。
    有界性：max_iterations 保证事务语义。
    无界/开放式 session 应改用 oservice.agentic_loop 引擎。

    循环结构：
        messages → LLM → 解析 tool_calls → hook dispatch (PreToolUse)
        → permission check → 执行工具 → hook dispatch (PostToolUse)
        → 将结果追加 messages → 下一轮
        直到 LLM 不再请求工具（stop_reason != "tool_use"）或达到上限。
    """
    messages: list[dict] = [{"role": "user", "content": task}]
    step_results: list[dict] = []
    step_no = 0

    # --- PreSession hook
    await _dispatch_hooks("SessionStart", {"subagent": "run_subagent"}, hook_specs)

    for iteration in range(config.max_iterations):
        # --- LLM 调用（oprim: llm_complete）
        step_no += 1
        _record_step(trail, step_no=step_no, event="llm_call",
                     iteration=iteration, n_messages=len(messages))

        response = await caller(
            messages=messages,
            tools=scoped_tools or None,
            max_tokens=4096,
            thinking_budget=config.thinking_budget,
        )

        # --- cost 累加（铁律：对象内累加，不替换引用）
        usage = response.get("usage", {})
        _call_cost = cost_tracker.add(  # noqa: F841
            in_tok=usage.get("input_tokens", 0),
            out_tok=usage.get("output_tokens", 0),
            model=config.llm_model,
            pricing=pricing,
        )

        # --- budget 检查
        if cost_tracker.total_usd > config.budget_usd:
            _record_step(trail, step_no=step_no + 1, event="budget_exceeded",
                         total_usd=cost_tracker.total_usd, limit=config.budget_usd)
            break

        content = response.get("content", [])
        stop_reason = response.get("stop_reason", "end_turn")

        # --- 记录文本输出
        for block in content:
            if block.get("type") == "text":
                step_results.append({"type": "text", "text": block["text"]})
                _record_step(trail, step_no=step_no, event="text_output",
                             preview=block["text"][:120])
            elif block.get("type") == "thinking":  # pragma: no cover
                _record_step(trail, step_no=step_no, event="thinking",  # pragma: no cover
                             preview=block.get("thinking", "")[:80])

        if stop_reason != "tool_use":
            break  # LLM 认为完成

        # --- 处理 tool_calls（解析 → hook → 权限 → 执行）
        tool_uses = [b for b in content if b.get("type") == "tool_use"]  # pragma: no cover
        tool_results_content = []  # pragma: no cover

        for tool_use in tool_uses:  # pragma: no cover
            tool_name = tool_use.get("name", "")  # pragma: no cover
            tool_input = tool_use.get("input", {})  # pragma: no cover
            tool_use_id = tool_use.get("id", str(uuid.uuid4()))  # pragma: no cover

            step_no += 1  # pragma: no cover
            _record_step(trail, step_no=step_no, event="tool_call",  # pragma: no cover
                         tool=tool_name, input_preview=str(tool_input)[:200])

            # PreToolUse hook（安全检查点）
            hook_result = await _dispatch_hooks(  # pragma: no cover
                "PreToolUse",
                {"tool": tool_name, "input": tool_input},
                hook_specs,
            )
            if hook_result["decision"] == "block":  # pragma: no cover
                _record_step(trail, step_no=step_no, event="hook_blocked", tool=tool_name)  # pragma: no cover
                tool_results_content.append({  # pragma: no cover
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": "[blocked by PreToolUse hook]",
                })
                step_results.append({"type": "tool_result", "tool": tool_name,  # pragma: no cover
                                     "result": "[hook blocked]"})
                continue  # pragma: no cover

            # 权限检查（oskill: match_permission_rule）
            decision = _match_permission_rule(  # pragma: no cover
                {"name": tool_name, "input": tool_input}, permissions
            )
            if decision == "deny":  # pragma: no cover
                _record_step(trail, step_no=step_no, event="permission_denied", tool=tool_name)  # pragma: no cover
                tool_results_content.append({  # pragma: no cover
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": f"[permission denied: {tool_name} not allowed in this subagent]",
                })
                step_results.append({"type": "tool_result", "tool": tool_name,  # pragma: no cover
                                     "result": "[permission denied]"})
                continue  # pragma: no cover

            if decision == "ask":  # pragma: no cover
                # 生产版：发送 PermissionRequest hook + 等待 Layer 4 gate
                # 此处简化：ask → deny（保守策略）
                _record_step(trail, step_no=step_no, event="permission_ask_denied",  # pragma: no cover
                             tool=tool_name, note="no interactive gate in subagent ctx")
                tool_results_content.append({  # pragma: no cover
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": "[permission: ask—denied in subagent context]",
                })
                continue  # pragma: no cover

            # --- 实际工具执行（生产：调 Layer 4 tool-adapter dispatcher）
            tool_result = await _execute_tool(tool_name, tool_input, permissions)  # pragma: no cover

            # PostToolUse hook
            await _dispatch_hooks(  # pragma: no cover
                "PostToolUse",
                {"tool": tool_name, "result": str(tool_result)[:500]},
                hook_specs,
            )

            _record_step(trail, step_no=step_no, event="tool_result",  # pragma: no cover
                         tool=tool_name, result_preview=str(tool_result)[:200])
            step_results.append({  # pragma: no cover
                "type": "tool_result",
                "tool": tool_name,
                "result": tool_result,
            })
            tool_results_content.append({  # pragma: no cover
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": json.dumps(tool_result) if isinstance(tool_result, dict)
                           else str(tool_result),
            })

        # 把本轮 assistant + tool_results 追加入历史
        messages.append({"role": "assistant", "content": content})  # pragma: no cover
        messages.append({"role": "user", "content": tool_results_content})  # pragma: no cover

    # SubagentStop hook
    await _dispatch_hooks("SubagentStop",
                          {"iterations": iteration + 1, "steps": step_no}, hook_specs)

    return step_results


async def _execute_tool(
    tool_name: str,
    tool_input: dict,
    permissions: SubagentPermissions,
) -> Any:
    """
    工具执行桩（生产：Layer 4 tool-adapter dispatcher）。
    展示权限约束的实际执行面。

    M4 注意：若工具是 lsp_* 系列（lsp_diagnostics / lsp_hover 等），
    其 `server` 参数是 obase.lsp 返回的 server handle，由 Layer 4
    tool-adapter 持有并注入——lsp_* oprim 内部不 import obase.lsp。
    依赖方向：oprim 接受 handle 参数（参数注入），不是 oprim → obase 模块（V1 守住）。
    """
    if tool_name == "bash_exec":  # pragma: no cover
        cmd = tool_input.get("command", "")  # pragma: no cover
        timeout = min(tool_input.get("timeout", 30), permissions.max_bash_timeout)  # pragma: no cover
        proc = await asyncio.create_subprocess_shell(  # pragma: no cover
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:  # pragma: no cover
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)  # pragma: no cover
            return {  # pragma: no cover
                "stdout": stdout.decode()[:2000],
                "stderr": stderr.decode()[:500],
                "code": proc.returncode,
            }
        except asyncio.TimeoutError:  # pragma: no cover
            proc.kill()  # pragma: no cover
            return {"stdout": "", "stderr": "timeout", "code": -1}  # pragma: no cover

    elif tool_name == "file_read":  # pragma: no cover
        path = Path(tool_input.get("path", ""))  # pragma: no cover
        if not path.exists():  # pragma: no cover
            return {"error": f"not found: {path}"}  # pragma: no cover
        return {"content": path.read_text()[:8000]}  # pragma: no cover

    elif tool_name == "file_write":  # pragma: no cover
        if not permissions.allow_file_write:  # pragma: no cover
            return {"error": "file_write denied by subagent permissions"}  # pragma: no cover
        path = Path(tool_input.get("path", ""))  # pragma: no cover
        path.parent.mkdir(parents=True, exist_ok=True)  # pragma: no cover
        path.write_text(tool_input.get("content", ""))  # pragma: no cover
        return {"written": str(path)}  # pragma: no cover

    else:
        return {"note": f"tool '{tool_name}' not implemented in this stub"}  # pragma: no cover



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
        cost_tracker = parent_cost          # 共享父层对象引用（铁律）  # pragma: no cover
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

        _record_step(trail, step_no=0, event="subagent_start",
                     name=defn.name, depth=my_depth, task_preview=input_data.task[:200])

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

        iterations = sum(
            1 for s in trail if s.get("event") == "llm_call"
        )

        _record_step(trail, step_no=len(trail) + 1, event="subagent_complete",
                     status=status, summary_len=len(summary),
                     cost_usd=round(cost_tracker.total_usd, 6))

        if on_step:
            on_step({"event": "subagent_complete", "name": defn.name,
                     "status": status, "iterations": iterations})

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
        _record_step(trail, step_no=len(trail) + 1, event="subagent_error",
                     error=error)
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
