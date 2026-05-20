"""Tests for 5 builtin agents (mock LLM + oskill)."""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from omodul.knowledge.agents.base import AgentContext, Citation
from omodul.knowledge.agents.runner import AgentRunner


def _ctx(user_id="u1", run_id="R001"):
    return AgentContext(
        user_id=user_id,
        agent_run_id=run_id,
        invoked_at=__import__("datetime").datetime.utcnow(),
    )


class _MockTracer:
    def create_run(self, **kw):
        pass

    def complete_run(self, **kw):
        pass


# ---- KnowledgeCuratorAgent ----

class TestKnowledgeCuratorAgent:
    @pytest.mark.asyncio
    async def test_empty_inbox_returns_ok(self, tmp_path):
        from omodul.knowledge.agents.builtin.knowledge_curator import KnowledgeCuratorAgent
        agent = KnowledgeCuratorAgent()
        ctx = _ctx()
        result = await agent.run({"inbox_dir": str(tmp_path)}, ctx)
        assert result.success
        assert result.output["files_found"] == 0

    @pytest.mark.asyncio
    async def test_ingest_fail_counts_as_failed(self, tmp_path):
        import omodul.knowledge.agents.builtin.knowledge_curator as _mod
        from omodul.knowledge.agents.builtin.knowledge_curator import KnowledgeCuratorAgent
        (tmp_path / "test.md").write_text("hello")
        agent = KnowledgeCuratorAgent()
        ctx = _ctx()
        with patch.object(_mod, "ingest_substrate", new=AsyncMock(side_effect=RuntimeError("parse error"))):
            result = await agent.run({"inbox_dir": str(tmp_path)}, ctx)
        assert result.output["failed"] == 1

    @pytest.mark.asyncio
    async def test_successful_ingest(self, tmp_path):
        import omodul.knowledge.agents.builtin.knowledge_curator as _mod
        from omodul.knowledge.agents.builtin.knowledge_curator import KnowledgeCuratorAgent
        (tmp_path / "note.md").write_text("# Note")
        agent = KnowledgeCuratorAgent()
        ctx = _ctx()

        mock_result = MagicMock(substrate_id="SUB001", medium="markdown_note", duplicate_of=None)

        with patch.object(_mod, "ingest_substrate", new=AsyncMock(return_value=mock_result)):
            result = await agent.run({"inbox_dir": str(tmp_path)}, ctx)

        assert result.success
        assert result.output["ingested"] == 1
        assert len(result.trace) >= 1

    @pytest.mark.asyncio
    async def test_duplicate_counts_as_skipped(self, tmp_path):
        import omodul.knowledge.agents.builtin.knowledge_curator as _mod
        from omodul.knowledge.agents.builtin.knowledge_curator import KnowledgeCuratorAgent
        (tmp_path / "dup.md").write_text("# Duplicate")
        agent = KnowledgeCuratorAgent()
        ctx = _ctx()

        mock_result = MagicMock(substrate_id="SUB001", medium="markdown_note", duplicate_of="SUB001")

        with patch.object(_mod, "ingest_substrate", new=AsyncMock(return_value=mock_result)):
            result = await agent.run({"inbox_dir": str(tmp_path)}, ctx)

        assert result.success
        assert result.output["skipped"] == 1
        assert result.output["ingested"] == 0


# ---- DailyDigestAgent ----

class TestDailyDigestAgent:
    @pytest.mark.asyncio
    async def test_no_recent_substrates(self):
        from omodul.knowledge.agents.builtin.daily_digest import DailyDigestAgent
        agent = DailyDigestAgent()
        ctx = _ctx()
        with patch.object(agent, "_list_recent_substrates", return_value=[]):
            result = await agent.run({}, ctx)
        assert result.success
        assert result.output["new_substrates"] == 0

    @pytest.mark.asyncio
    async def test_with_substrates_calls_llm(self):
        import omodul.knowledge.agents.builtin.daily_digest as _mod
        from omodul.knowledge.agents.builtin.daily_digest import DailyDigestAgent
        from oprim.llm import LLMResponse
        agent = DailyDigestAgent()
        ctx = _ctx()

        fake_subs = [{"id": "S001", "title": "Paper A", "created_at": "2026-05-20"}]
        fake_resp = LLMResponse(
            text="今日新增：Paper A。", model="test", input_tokens=10, output_tokens=15, cost_usd=0.001
        )

        with (
            patch.object(agent, "_list_recent_substrates", return_value=fake_subs),
            patch.object(_mod, "llm_call", return_value=fake_resp),
            patch.object(agent, "_get_dispatcher", return_value=None),
        ):
            result = await agent.run({}, ctx)

        assert result.success
        assert result.output["new_substrates"] == 1
        assert len(result.citations) == 1
        assert result.citations[0].substrate_id == "S001"
        assert result.total_input_tokens == 10


# ---- ReadingCompanionAgent ----

class TestReadingCompanionAgent:
    @pytest.mark.asyncio
    async def test_missing_question_raises(self):
        from omodul.knowledge.agents.builtin.reading_companion import ReadingCompanionAgent
        agent = ReadingCompanionAgent()
        ctx = _ctx()
        with pytest.raises(ValueError, match="question"):
            await agent.run({}, ctx)

    @pytest.mark.asyncio
    async def test_answer_returned(self):
        import omodul.knowledge.agents.builtin.reading_companion as _mod
        from omodul.knowledge.agents.builtin.reading_companion import ReadingCompanionAgent
        from oprim.llm import LLMResponse
        agent = ReadingCompanionAgent()
        ctx = _ctx()

        fake_hit = MagicMock(id="S002", title="Doc B", highlight="content", citation=None)
        fake_resp = LLMResponse(
            text="答案在此。[S002]", model="test", input_tokens=20, output_tokens=8, cost_usd=0.002
        )

        with (
            patch.object(_mod, "hybrid_search", new=AsyncMock(return_value=[fake_hit])),
            patch.object(_mod, "llm_call", return_value=fake_resp),
        ):
            result = await agent.run({"question": "什么是夏普比率？"}, ctx)

        assert result.success
        assert "答案在此" in result.output["answer"]
        assert len(result.citations) == 1
        assert result.citations[0].substrate_id == "S002"


# ---- TranslationWorkerAgent ----

class TestTranslationWorkerAgent:
    @pytest.mark.asyncio
    async def test_no_candidates_returns_success(self):
        from omodul.knowledge.agents.builtin.translation_worker import TranslationWorkerAgent
        agent = TranslationWorkerAgent()
        ctx = _ctx()
        with patch.object(agent, "_find_candidates", return_value=[]):
            result = await agent.run({"max_substrates": 3}, ctx)
        assert result.success
        assert result.output["candidates"] == 0

    @pytest.mark.asyncio
    async def test_translates_candidates(self):
        import omodul.knowledge.agents.builtin.translation_worker as _mod
        from omodul.knowledge.agents.builtin.translation_worker import TranslationWorkerAgent
        agent = TranslationWorkerAgent()
        ctx = _ctx()

        with (
            patch.object(agent, "_find_candidates", return_value=["S001", "S002"]),
            patch.object(_mod, "translate_substrate", new=AsyncMock(return_value=MagicMock(derivative_id="D001", cost_usd=0.05, chunks_translated=3))),
        ):
            result = await agent.run({"max_substrates": 5}, ctx)

        assert result.success
        assert result.output["translated"] == 2
        assert len(result.citations) == 2
        assert result.cost_usd == pytest.approx(0.10)


# ---- LintBotAgent ----

class TestLintBotAgent:
    @pytest.mark.asyncio
    async def test_no_issues(self):
        import omodul.knowledge.agents.builtin.lint_bot as _mod
        from omodul.knowledge.agents.builtin.lint_bot import LintBotAgent
        agent = LintBotAgent()
        ctx = _ctx()
        with patch.object(_mod, "lint", new=AsyncMock(return_value=[])):
            result = await agent.run({}, ctx)
        assert result.success
        assert result.output["issues_count"] == 0

    @pytest.mark.asyncio
    async def test_issues_included_in_output(self):
        import omodul.knowledge.agents.builtin.lint_bot as _mod
        from omodul.knowledge.agents.builtin.lint_bot import LintBotAgent
        agent = LintBotAgent()
        ctx = _ctx()
        fake_issue = MagicMock(type="orphan_embedding", description="S001 has no embedding", severity="error")
        with (
            patch.object(_mod, "lint", new=AsyncMock(return_value=[fake_issue])),
            patch.object(agent, "_get_dispatcher", return_value=None),
        ):
            result = await agent.run({}, ctx)
        assert result.output["issues_count"] == 1
        assert result.output["issues"][0]["type"] == "orphan_embedding"
