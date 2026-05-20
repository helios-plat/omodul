"""Tests for AgentRunner and AgentTracer."""
from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from omodul.knowledge.agents.base import Agent, AgentContext, AgentResult, AgentStep, Citation
from omodul.knowledge.agents.runner import AgentRunner
from omodul.knowledge.agents.tracer import AgentTracer


class _OkAgent(Agent):
    name = "ok_agent"
    description = "Always succeeds"
    allowed_tools = []

    async def run(self, params, context):
        return AgentResult(
            success=True,
            output={"done": True},
            trace=[
                AgentStep(step_num=1, tool_name="noop", tool_output={"x": 1}, duration_ms=5)
            ],
            citations=[Citation(substrate_id="SUB001")],
            total_input_tokens=10,
            total_output_tokens=20,
            cost_usd=0.001,
        )


class _FailAgent(Agent):
    name = "fail_agent"
    description = "Always fails"
    allowed_tools = []

    async def run(self, params, context):
        raise RuntimeError("deliberate failure")


class _SlowAgent(Agent):
    name = "slow_agent"
    description = "Times out"
    allowed_tools = []
    timeout_seconds = 0  # type: ignore[assignment]

    async def run(self, params, context):
        await asyncio.sleep(10)
        return AgentResult(success=True, output={}, trace=[], citations=[])


class _MockTracer:
    def __init__(self):
        self.created: list[dict] = []
        self.completed: list[dict] = []

    def create_run(self, **kwargs):
        self.created.append(kwargs)

    def complete_run(self, **kwargs):
        self.completed.append(kwargs)


class TestAgentRunner:
    @pytest.mark.asyncio
    async def test_successful_run_creates_and_completes(self):
        tracer = _MockTracer()
        runner = AgentRunner(tracer)
        agent = _OkAgent()
        result = await runner.run(agent, "user1", {})
        assert result.success
        assert result.output == {"done": True}
        assert len(tracer.created) == 1
        assert len(tracer.completed) == 1
        assert tracer.completed[0]["status"] == "completed"

    @pytest.mark.asyncio
    async def test_failed_run_marks_status_failed(self):
        tracer = _MockTracer()
        runner = AgentRunner(tracer)
        agent = _FailAgent()
        with pytest.raises(RuntimeError):
            await runner.run(agent, "user1", {})
        assert tracer.completed[0]["status"] == "failed"
        assert "deliberate failure" in tracer.completed[0]["error_message"]

    @pytest.mark.asyncio
    async def test_timeout_marks_status_timeout(self):
        tracer = _MockTracer()
        runner = AgentRunner(tracer)
        agent = _SlowAgent()
        with pytest.raises(asyncio.TimeoutError):
            await runner.run(agent, "user1", {})
        assert tracer.completed[0]["status"] == "timeout"

    @pytest.mark.asyncio
    async def test_result_has_elapsed_seconds(self):
        tracer = _MockTracer()
        runner = AgentRunner(tracer)
        agent = _OkAgent()
        result = await runner.run(agent, "user1", {})
        assert result.elapsed_seconds >= 0

    @pytest.mark.asyncio
    async def test_context_has_correct_user_id(self):
        received_contexts: list[AgentContext] = []

        class _CtxCapture(Agent):
            name = "ctx_capture"
            description = ""
            allowed_tools = []

            async def run(self, params, context):
                received_contexts.append(context)
                return AgentResult(success=True, output={}, trace=[], citations=[])

        tracer = _MockTracer()
        runner = AgentRunner(tracer)
        await runner.run(_CtxCapture(), "user_xyz", {"foo": "bar"})
        assert received_contexts[0].user_id == "user_xyz"


class TestAgentTracer:
    def _make_tracer(self, tmp_path: Path) -> AgentTracer:
        from oprim.meta_db import MetaDB
        db_path = tmp_path / "test.duckdb"
        db = MetaDB(db_path)
        # Apply migration
        migrations_dir = (
            Path(__file__).parent.parent.parent.parent.parent.parent
            / "oprim" / "oprim" / "meta_db" / "migrations"
        )
        if migrations_dir.exists():
            db.migrate(migrations_dir)
        else:
            db.execute("""
                CREATE TABLE IF NOT EXISTS agent_runs (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, agent_name TEXT NOT NULL,
                    params TEXT NOT NULL, status TEXT NOT NULL, trace TEXT, citations TEXT,
                    output TEXT, total_input_tokens INTEGER DEFAULT 0,
                    total_output_tokens INTEGER DEFAULT 0, cost_usd REAL DEFAULT 0.0,
                    started_at TIMESTAMP NOT NULL, completed_at TIMESTAMP, error_message TEXT
                )
            """)
        return AgentTracer(db=db)

    def test_create_and_complete_run(self, tmp_path):
        tracer = self._make_tracer(tmp_path)
        tracer.create_run(
            run_id="R001",
            user_id="u1",
            agent_name="test_agent",
            params={"a": 1},
            started_at=datetime.utcnow(),
        )
        tracer.complete_run(
            run_id="R001",
            status="completed",
            trace=[AgentStep(step_num=1, tool_name="t", duration_ms=5)],
            citations=[Citation(substrate_id="S001")],
            output={"done": True},
            total_input_tokens=5,
            total_output_tokens=10,
            cost_usd=0.001,
            completed_at=datetime.utcnow(),
        )
        row = tracer.get_run("R001")
        assert row is not None
        assert row["status"] == "completed"
        assert row["agent_name"] == "test_agent"

    def test_get_run_not_found(self, tmp_path):
        tracer = self._make_tracer(tmp_path)
        assert tracer.get_run("NONEXISTENT") is None

    def test_list_runs(self, tmp_path):
        tracer = self._make_tracer(tmp_path)
        for i in range(3):
            tracer.create_run(
                run_id=f"R{i:03d}",
                user_id="u1",
                agent_name="agent_a",
                params={},
                started_at=datetime.utcnow(),
            )
            tracer.complete_run(run_id=f"R{i:03d}", status="completed")
        runs = tracer.list_runs("u1")
        assert len(runs) == 3
