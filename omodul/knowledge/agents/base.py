"""Agent base class + shared dataclasses."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

from .errors import AgentToolNotAllowedError


@dataclass
class Citation:
    substrate_id: str
    title: str = ""
    fragment_id: str | None = None
    anchor: dict | None = None
    deep_link: str | None = None


@dataclass
class AgentStep:
    step_num: int
    tool_name: str
    tool_input: dict = field(default_factory=dict)
    tool_output: dict | None = None
    duration_ms: int = 0
    error: str | None = None
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass
class AgentResult:
    success: bool
    output: dict
    trace: list[AgentStep]
    citations: list[Citation]
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    elapsed_seconds: float = 0.0
    cost_usd: float = 0.0
    error: str | None = None


@dataclass
class AgentContext:
    user_id: str
    agent_run_id: str
    invoked_at: datetime
    metadata: dict = field(default_factory=dict)


class Agent(ABC):
    """Abstract base for all Stratum builtin agents."""

    name: str = ""
    description: str = ""
    allowed_tools: list[str] = []
    llm_provider: str = "qwen3_dashscope"
    llm_model: str = "qwen-max"
    temperature: float = 0.2
    timeout_seconds: int = 1800

    @abstractmethod
    async def run(self, params: dict, context: AgentContext) -> AgentResult:
        """Execute the agent and return a result with full trace."""
        ...

    def _verify_tool_allowed(self, tool_name: str) -> None:
        if tool_name not in self.allowed_tools:
            raise AgentToolNotAllowedError(
                f"{self.name!r} attempted to call disallowed tool: {tool_name!r}"
            )
