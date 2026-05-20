"""Tests for agents base, registry, and errors."""
from __future__ import annotations

import pytest

from omodul.knowledge.agents.base import Agent, AgentContext, AgentResult, AgentStep, Citation
from omodul.knowledge.agents.errors import AgentNotFoundError, AgentToolNotAllowedError
from omodul.knowledge.agents.registry import AgentRegistry, register_agent


class _DummyAgent(Agent):
    name = "dummy_test"
    description = "Test agent"
    allowed_tools = ["tool_a", "tool_b"]

    async def run(self, params, context):
        return AgentResult(success=True, output={}, trace=[], citations=[])


class TestAgentBase:
    def test_verify_tool_allowed_ok(self):
        agent = _DummyAgent()
        agent._verify_tool_allowed("tool_a")  # no exception

    def test_verify_tool_not_allowed_raises(self):
        agent = _DummyAgent()
        with pytest.raises(AgentToolNotAllowedError):
            agent._verify_tool_allowed("forbidden_tool")


class TestAgentRegistry:
    def test_register_and_get(self):
        reg = AgentRegistry()
        reg.register(_DummyAgent)
        cls = reg.get("dummy_test")
        assert cls is _DummyAgent

    def test_get_unknown_raises(self):
        reg = AgentRegistry()
        with pytest.raises(AgentNotFoundError):
            reg.get("no_such_agent")

    def test_register_missing_name_raises(self):
        class NoName(Agent):
            name = ""
            description = ""
            allowed_tools = []

            async def run(self, params, context):
                return AgentResult(success=True, output={}, trace=[], citations=[])

        reg = AgentRegistry()
        with pytest.raises(ValueError):
            reg.register(NoName)

    def test_list_agents(self):
        reg = AgentRegistry()
        reg.register(_DummyAgent)
        listing = reg.list_agents()
        assert any(a["name"] == "dummy_test" for a in listing)

    def test_contains(self):
        reg = AgentRegistry()
        reg.register(_DummyAgent)
        assert "dummy_test" in reg
        assert "other" not in reg


class TestBuiltinRegistration:
    """Verify all 5 builtin agents auto-register on import."""

    def test_all_builtins_registered(self):
        from omodul.knowledge.agents import get_registry
        reg = get_registry()
        for name in [
            "knowledge_curator",
            "daily_digest",
            "reading_companion",
            "translation_worker",
            "lint_bot",
        ]:
            assert name in reg, f"Agent '{name}' not registered"

    def test_builtin_listing_has_required_fields(self):
        from omodul.knowledge.agents import get_registry
        agents = get_registry().list_agents()
        for a in agents:
            assert "name" in a
            assert "description" in a
            assert "allowed_tools" in a
