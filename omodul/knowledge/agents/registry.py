"""Agent registry — maps agent names to classes."""
from __future__ import annotations

from typing import Type

from .base import Agent
from .errors import AgentNotFoundError


class AgentRegistry:
    """Registry of all available builtin agents."""

    def __init__(self) -> None:
        self._agents: dict[str, Type[Agent]] = {}

    def register(self, agent_cls: Type[Agent]) -> None:
        if not agent_cls.name:
            raise ValueError(f"{agent_cls.__name__} must define a non-empty 'name'")
        self._agents[agent_cls.name] = agent_cls

    def get(self, name: str) -> Type[Agent]:
        if name not in self._agents:
            raise AgentNotFoundError(f"Agent not registered: {name!r}")
        return self._agents[name]

    def list_agents(self) -> list[dict]:
        return [
            {
                "name": cls.name,
                "description": cls.description,
                "allowed_tools": cls.allowed_tools,
                "timeout_seconds": cls.timeout_seconds,
            }
            for cls in self._agents.values()
        ]

    def __contains__(self, name: str) -> bool:
        return name in self._agents


_global_registry = AgentRegistry()


def get_registry() -> AgentRegistry:
    return _global_registry


def register_agent(cls: Type[Agent]) -> Type[Agent]:
    """Class decorator: @register_agent"""
    _global_registry.register(cls)
    return cls
