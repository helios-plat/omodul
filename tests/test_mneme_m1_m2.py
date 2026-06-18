"""Tests for M-1 and M-2 of the Mneme speech/essay batch.

M-1: speaking_practice_workflow  (≥10 tests)
M-2: essay_review_workflow       (≥10 tests)
"""

from __future__ import annotations

import asyncio
import base64
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dataclasses import dataclass


@dataclass
class PronunciationResult:
    overall_score: float
    fluency_score: float
    accuracy_score: float
    word_scores: list


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _b64(text: str = "audio") -> str:
    return base64.b64encode(text.encode()).decode()


def _pron(overall: float = 0.8) -> PronunciationResult:
    return PronunciationResult(overall_score=overall, fluency_score=0.75, accuracy_score=0.85, word_scores=[])


def _make_tts():
    return AsyncMock(return_value=_b64("tts-out"))


def _make_stt(text: str = "I like English"):
    return AsyncMock(return_value=text)


def _make_pron_eval(overall: float = 0.8):
    return AsyncMock(return_value=_pron(overall))


def _make_llm(text: str = "Well done! What else？"):
    async def caller(*, messages, max_tokens=256, **kwargs):
        return {"content": [{"type": "text", "text": text}], "usage": {}}
    return caller


def _make_essay_llm(questions=None):
    if questions is None:
        questions = ["你认为论点还可以如何加强？", "例子是否足够有说服力？", "结尾传达了什么核心信息？"]

    async def caller(*, messages, max_tokens=512, **kwargs):
        return {"content": [{"type": "text", "text": json.dumps(questions, ensure_ascii=False)}], "usage": {}}
    return caller


_SAMPLE_ESSAY = """\
科技的快速发展为现代社会带来了深刻的变革。从互联网到人工智能，技术已经渗透到了生活的每一个角落。

然而，我们不能忽视科技带来的潜在风险。数据隐私、信息泡沫和技术垄断都是需要认真面对的问题。

我相信，只有在理性地把握科技发展方向的同时，加强人文关怀，才能让科技真正造福于人类。
""".strip()


# ===========================================================================
# M-1: speaking_practice_workflow
# ===========================================================================

class TestSpeakingPracticeWorkflow:
    def _make_config(self, turns=1):
        from omodul.speaking_practice_workflow import Config
        return Config(max_turns=turns)

    def _make_input(self, topic="Sports", db_pool=None):
        from omodul.speaking_practice_workflow import InputData
        return InputData(
            topic=topic,
            user_id="u001",
            tts=_make_tts(),
            stt=_make_stt(),
            pronunciation_eval=_make_pron_eval(),
            llm_caller=_make_llm(),
            db_pool=db_pool,
        )

    async def test_returns_completed_status(self, tmp_path):
        from omodul.speaking_practice_workflow import speaking_practice_workflow

        result = await speaking_practice_workflow(
            self._make_config(), self._make_input(), tmp_path
        )
        assert result["status"] == "completed"

    async def test_result_has_session_id(self, tmp_path):
        from omodul.speaking_practice_workflow import speaking_practice_workflow

        result = await speaking_practice_workflow(
            self._make_config(), self._make_input(), tmp_path
        )
        assert "session_id" in result
        assert len(result["session_id"]) == 16

    async def test_result_has_cost_usd(self, tmp_path):
        from omodul.speaking_practice_workflow import speaking_practice_workflow

        result = await speaking_practice_workflow(
            self._make_config(), self._make_input(), tmp_path
        )
        assert "cost_usd" in result
        assert isinstance(result["cost_usd"], float)

    async def test_result_has_decision_trail(self, tmp_path):
        from omodul.speaking_practice_workflow import speaking_practice_workflow

        result = await speaking_practice_workflow(
            self._make_config(), self._make_input(), tmp_path
        )
        assert "decision_trail" in result
        assert result["decision_trail"]["steps"] >= 2

    async def test_trail_file_written(self, tmp_path):
        from omodul.speaking_practice_workflow import speaking_practice_workflow

        await speaking_practice_workflow(self._make_config(), self._make_input(), tmp_path)
        trail_files = list(tmp_path.glob("decision_trail_*.json"))
        assert len(trail_files) >= 1

    async def test_overall_progress_in_result(self, tmp_path):
        from omodul.speaking_practice_workflow import speaking_practice_workflow

        result = await speaking_practice_workflow(
            self._make_config(turns=2), self._make_input(), tmp_path
        )
        assert "overall_progress" in result
        assert 0.0 <= result["overall_progress"] <= 1.0

    async def test_cancelled_error_propagates(self, tmp_path):
        from omodul.speaking_practice_workflow import speaking_practice_workflow, Config, InputData

        async def failing_tts(**kwargs):
            raise asyncio.CancelledError()

        inp = InputData(
            topic="Test",
            tts=failing_tts,
            stt=_make_stt(),
            pronunciation_eval=_make_pron_eval(),
            llm_caller=_make_llm(),
        )
        with pytest.raises(asyncio.CancelledError):
            await speaking_practice_workflow(Config(max_turns=1), inp, tmp_path)

    async def test_exception_returns_failed_status(self, tmp_path):
        from omodul.speaking_practice_workflow import speaking_practice_workflow, Config, InputData

        async def broken_tts(**kwargs):
            raise RuntimeError("provider down")

        inp = InputData(
            topic="Test",
            tts=broken_tts,
            stt=_make_stt(),
            pronunciation_eval=_make_pron_eval(),
            llm_caller=_make_llm(),
        )
        result = await speaking_practice_workflow(Config(max_turns=1), inp, tmp_path)
        assert result["status"] == "failed"
        assert "provider down" in result["error"]["message"]

    async def test_persistence_with_pool_completes(self, tmp_path):
        """Workflow completes successfully even when db_pool is provided."""
        from omodul.speaking_practice_workflow import speaking_practice_workflow

        mock_pool = MagicMock()
        mock_insert = AsyncMock()
        # Patch at the obase level so insert_one is captured
        with patch("obase.persistence.insert_one", mock_insert):
            result = await speaking_practice_workflow(
                self._make_config(), self._make_input(db_pool=mock_pool), tmp_path
            )
        assert result["status"] == "completed"

    async def test_turns_count_in_result(self, tmp_path):
        from omodul.speaking_practice_workflow import speaking_practice_workflow

        result = await speaking_practice_workflow(
            self._make_config(turns=3), self._make_input(), tmp_path
        )
        assert result["turns"] == 3


# ===========================================================================
# M-2: essay_review_workflow
# ===========================================================================

class TestEssayReviewWorkflow:
    def _make_config(self):
        from omodul.essay_review_workflow import Config
        return Config()

    def _make_input(self, essay=_SAMPLE_ESSAY):
        from omodul.essay_review_workflow import InputData
        return InputData(
            essay_text=essay,
            grade_level="高中",
            essay_type="议论文",
            user_id="u002",
            llm_caller=_make_essay_llm(),
        )

    async def test_returns_completed_status(self, tmp_path):
        from omodul.essay_review_workflow import essay_review_workflow

        result = await essay_review_workflow(self._make_config(), self._make_input(), tmp_path)
        assert result["status"] == "completed"

    async def test_fingerprint_in_result(self, tmp_path):
        from omodul.essay_review_workflow import essay_review_workflow

        result = await essay_review_workflow(self._make_config(), self._make_input(), tmp_path)
        assert "fingerprint" in result
        assert len(result["fingerprint"]) == 24

    async def test_fingerprint_deterministic(self, tmp_path):
        from omodul.essay_review_workflow import compute_fingerprint_for_essay_review_workflow

        f1 = compute_fingerprint_for_essay_review_workflow("同一篇文章", "高中", "议论文")
        f2 = compute_fingerprint_for_essay_review_workflow("同一篇文章", "高中", "议论文")
        assert f1 == f2

    async def test_report_file_written(self, tmp_path):
        from omodul.essay_review_workflow import essay_review_workflow

        result = await essay_review_workflow(self._make_config(), self._make_input(), tmp_path)
        assert "report_path" in result
        report = Path(result["report_path"])
        assert report.exists()
        content = report.read_text(encoding="utf-8")
        assert "维度得分" in content

    async def test_report_has_no_model_essay(self, tmp_path):
        from omodul.essay_review_workflow import essay_review_workflow

        result = await essay_review_workflow(self._make_config(), self._make_input(), tmp_path)
        report_text = Path(result["report_path"]).read_text(encoding="utf-8")
        # Model essay sections are forbidden; incidental references (e.g. "不要参考范文") are OK
        forbidden = ["示例范文", "改写后内容", "参考答案", "示例答案", "优秀范文"]
        for word in forbidden:
            assert word not in report_text, f"Report must not contain '{word}'"

    async def test_report_contains_guidance_questions(self, tmp_path):
        from omodul.essay_review_workflow import essay_review_workflow

        result = await essay_review_workflow(self._make_config(), self._make_input(), tmp_path)
        report_text = Path(result["report_path"]).read_text(encoding="utf-8")
        assert "引导性问题" in report_text

    async def test_decision_trail_in_result(self, tmp_path):
        from omodul.essay_review_workflow import essay_review_workflow

        result = await essay_review_workflow(self._make_config(), self._make_input(), tmp_path)
        assert "decision_trail" in result
        assert result["decision_trail"]["steps"] >= 2

    async def test_cancelled_error_propagates(self, tmp_path):
        from omodul.essay_review_workflow import essay_review_workflow, Config, InputData

        async def cancel_llm(*, messages, max_tokens=512, **kwargs):
            raise asyncio.CancelledError()

        inp = InputData(essay_text=_SAMPLE_ESSAY, llm_caller=cancel_llm)
        with pytest.raises(asyncio.CancelledError):
            await essay_review_workflow(Config(), inp, tmp_path)

    async def test_empty_essay_returns_failed(self, tmp_path):
        from omodul.essay_review_workflow import essay_review_workflow

        result = await essay_review_workflow(
            self._make_config(), self._make_input(essay=""), tmp_path
        )
        assert result["status"] == "failed"

    async def test_rubric_scores_in_result(self, tmp_path):
        from omodul.essay_review_workflow import essay_review_workflow

        result = await essay_review_workflow(self._make_config(), self._make_input(), tmp_path)
        assert "rubric_scores" in result
        assert set(result["rubric_scores"].keys()) == {"结构", "立意", "语言", "格式"}

    async def test_guidance_questions_in_result_end_with_question_mark(self, tmp_path):
        from omodul.essay_review_workflow import essay_review_workflow

        result = await essay_review_workflow(self._make_config(), self._make_input(), tmp_path)
        for q in result["guidance_questions"]:
            assert q.endswith("？"), f"Question must end with ？: {q!r}"

    async def test_revision_needed_in_result(self, tmp_path):
        from omodul.essay_review_workflow import essay_review_workflow

        result = await essay_review_workflow(self._make_config(), self._make_input(), tmp_path)
        assert "revision_needed" in result
        assert isinstance(result["revision_needed"], bool)
