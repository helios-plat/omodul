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
