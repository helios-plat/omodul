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

def compute_fingerprint_for(config: SubagentConfig, input_data: SubagentInput) -> str:
    """
    fingerprint 未启用（_enabled_pillars 不含 fingerprint）。
    若业务层需要子 agent 去重，可在 Layer 4 自行计算。
    此处提供实现备用。
    """
    import hashlib  # pragma: no cover
    key = json.dumps({  # pragma: no cover
        "task": input_data.task,
        "subagent": input_data.subagent_def.name,
        "model": config.llm_model,
    }, sort_keys=True)
    return hashlib.sha256(key.encode()).hexdigest()[:16]  # pragma: no cover
