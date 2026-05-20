"""omodul.knowledge.agents — Agent infrastructure + 5 builtin agents."""
from omodul.knowledge.agents.base import (
    Agent,
    AgentContext,
    AgentResult,
    AgentStep,
    Citation,
)
from omodul.knowledge.agents.errors import (
    AgentError,
    AgentNotFoundError,
    AgentTimeoutError,
    AgentToolNotAllowedError,
)
from omodul.knowledge.agents.registry import AgentRegistry, get_registry, register_agent
from omodul.knowledge.agents.runner import AgentRunner
from omodul.knowledge.agents.tracer import AgentTracer

# Import builtin agents so they self-register via @register_agent
import omodul.knowledge.agents.builtin  # noqa: F401

__all__ = [
    # Base
    "Agent",
    "AgentContext",
    "AgentResult",
    "AgentStep",
    "Citation",
    # Runner + tracer
    "AgentRunner",
    "AgentTracer",
    # Registry
    "AgentRegistry",
    "get_registry",
    "register_agent",
    # Errors
    "AgentError",
    "AgentNotFoundError",
    "AgentTimeoutError",
    "AgentToolNotAllowedError",
]
