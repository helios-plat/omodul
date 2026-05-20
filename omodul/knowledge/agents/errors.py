"""Agent-domain errors."""
from __future__ import annotations

from oprim.errors import StratumError


class AgentError(StratumError):
    """Generic agent execution failure."""


class AgentToolNotAllowedError(AgentError):
    """Agent attempted to call a tool outside its allow-list."""


class AgentTimeoutError(AgentError):
    """Agent exceeded its timeout."""


class AgentNotFoundError(AgentError):
    """Requested agent is not registered."""
