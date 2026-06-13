"""omodul M-E batch tests: 9 Mneme omodul elements.

≥10 tests per element, LLM fully mocked.
Mandatory: test_deleted_user_not_queryable
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from omodul.knowledge_profiling_workflow import (
    KnowledgeProfilingConfig,
    KnowledgeProfilingInput,
    knowledge_profiling_workflow,
)
from omodul.adaptive_quiz_session import (
    AdaptiveQuizConfig,
    AdaptiveQuizInput,
    adaptive_quiz_session,
)
from omodul.socratic_tutor_session import (
    SocraticTutorConfig,
    SocraticTutorInput,
    socratic_tutor_session,
)
from omodul.grade_paper_workflow import (
    GradePaperConfig,
    GradePaperInput,
    PaperQuestion,
    grade_paper_workflow,
)
from omodul.daily_mission_workflow import (
    DailyMissionConfig,
    DailyMissionInput,
    daily_mission_workflow,
)
from omodul.variant_generation_workflow import (
    VariantGenerationConfig,
    VariantGenerationInput,
    VariantSource,
    variant_generation_workflow,
)
from omodul.learning_progress_report import (
    LearningProgressConfig,
    ProgressInput,
    learning_progress_report,
)
from omodul.breakpoint_remediation_workflow import (
    BreakpointRemediationConfig,
    BreakpointRemediationInput,
    WrongQuestionEntry,
    breakpoint_remediation_workflow,
)
from omodul.user_data_workflow import (
    UserDataConfig,
    UserDataInput,
    UserRecord,
    reset_store,
    user_data_workflow,
)


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------

def make_caller(text="ok"):
    async def caller(**kwargs):
        return {
            "content": [{"type": "text", "text": text}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 20},
        }
    return caller


def make_json_caller(data: dict):
    import json
    return make_caller(json.dumps(data))


# ---------------------------------------------------------------------------
# ME-1: knowledge_profiling_workflow
# ---------------------------------------------------------------------------

class TestKnowledgeProfilingWorkflow:
    def _attempts(self, kc_id, correct_seq):
        return [{"kc_id": kc_id, "correct": c} for c in correct_seq]

    def test_status_ok(self, tmp_path):
        cfg = KnowledgeProfilingConfig()
        inp = KnowledgeProfilingInput(
            user_id="u1",
            attempt_history=self._attempts("algebra", [True, True, True]),
        )
        r = asyncio.run(knowledge_profiling_workflow(cfg, inp, tmp_path))
        assert r["status"] == "ok"

    def test_mastery_map_computed(self, tmp_path):
        cfg = KnowledgeProfilingConfig(min_attempts_for_mastery=2)
        inp = KnowledgeProfilingInput(
            user_id="u1",
            attempt_history=self._attempts("algebra", [True, True]),
        )
        r = asyncio.run(knowledge_profiling_workflow(cfg, inp, tmp_path))
        assert "algebra" in r["mastery_map"]
        assert r["mastery_map"]["algebra"] == 1.0

    def test_weak_kcs_identified(self, tmp_path):
        cfg = KnowledgeProfilingConfig(mastery_threshold=0.8, min_attempts_for_mastery=2)
        attempts = self._attempts("A", [False, False]) + self._attempts("B", [True, True])
        inp = KnowledgeProfilingInput(user_id="u1", attempt_history=attempts)
        r = asyncio.run(knowledge_profiling_workflow(cfg, inp, tmp_path))
        assert "A" in r["weak_kcs"]
        assert "B" in r["strong_kcs"]

    def test_report_written(self, tmp_path):
        cfg = KnowledgeProfilingConfig(min_attempts_for_mastery=1)
        inp = KnowledgeProfilingInput(
            user_id="u1", attempt_history=self._attempts("x", [True])
        )
        r = asyncio.run(knowledge_profiling_workflow(cfg, inp, tmp_path))
        if r.get("report_path"):
            assert Path(r["report_path"]).exists()

    def test_fingerprint_present(self, tmp_path):
        cfg = KnowledgeProfilingConfig()
        inp = KnowledgeProfilingInput(user_id="u42", attempt_history=[])
        r = asyncio.run(knowledge_profiling_workflow(cfg, inp, tmp_path))
        assert "fingerprint" in r

    def test_overall_mastery_zero_when_no_attempts(self, tmp_path):
        cfg = KnowledgeProfilingConfig()
        inp = KnowledgeProfilingInput(user_id="u1", attempt_history=[])
        r = asyncio.run(knowledge_profiling_workflow(cfg, inp, tmp_path))
        assert r["overall_mastery"] == 0.0

    def test_cost_zero(self, tmp_path):
        cfg = KnowledgeProfilingConfig()
        inp = KnowledgeProfilingInput(user_id="u1", attempt_history=[])
        r = asyncio.run(knowledge_profiling_workflow(cfg, inp, tmp_path))
        assert r["cost_usd"] == 0.0

    def test_min_attempts_filter(self, tmp_path):
        cfg = KnowledgeProfilingConfig(min_attempts_for_mastery=5)
        inp = KnowledgeProfilingInput(
            user_id="u1", attempt_history=self._attempts("short", [True, True])
        )
        r = asyncio.run(knowledge_profiling_workflow(cfg, inp, tmp_path))
        assert "short" not in r["mastery_map"]

    def test_on_step_called(self, tmp_path):
        steps = []
        cfg = KnowledgeProfilingConfig()
        inp = KnowledgeProfilingInput(user_id="u1", attempt_history=[])
        asyncio.run(knowledge_profiling_workflow(cfg, inp, tmp_path, on_step=lambda *a: steps.append(a)))
        assert len(steps) > 0

    def test_trail_written(self, tmp_path):
        cfg = KnowledgeProfilingConfig()
        inp = KnowledgeProfilingInput(user_id="u1", attempt_history=[])
        asyncio.run(knowledge_profiling_workflow(cfg, inp, tmp_path))
        trail_files = list(tmp_path.glob("decision_trail_*.json"))
        assert len(trail_files) >= 1


# ---------------------------------------------------------------------------
# ME-2: adaptive_quiz_session
# ---------------------------------------------------------------------------

class TestAdaptiveQuizSession:
    def _bank(self, n=8):
        kcs = ["A", "B", "C", "D"]
        return [
            {"question_id": f"q{i}", "kc_id": kcs[i % 4], "difficulty": 0.5, "mastery": 0.3}
            for i in range(n)
        ]

    def test_status_ok(self, tmp_path):
        cfg = AdaptiveQuizConfig(target_count=4)
        inp = AdaptiveQuizInput(user_id="u1", question_bank=self._bank())
        r = asyncio.run(adaptive_quiz_session(cfg, inp, tmp_path))
        assert r["status"] == "ok"

    def test_questions_returned(self, tmp_path):
        cfg = AdaptiveQuizConfig(target_count=4)
        inp = AdaptiveQuizInput(user_id="u1", question_bank=self._bank())
        r = asyncio.run(adaptive_quiz_session(cfg, inp, tmp_path))
        assert len(r["questions"]) <= 4

    def test_no_adjacent_same_kc(self, tmp_path):
        cfg = AdaptiveQuizConfig(target_count=8)
        inp = AdaptiveQuizInput(user_id="u1", question_bank=self._bank(8))
        r = asyncio.run(adaptive_quiz_session(cfg, inp, tmp_path))
        selected = r["questions"]
        for i in range(len(selected) - 1):
            assert selected[i]["kc_id"] != selected[i + 1]["kc_id"]

    def test_empty_bank(self, tmp_path):
        cfg = AdaptiveQuizConfig()
        inp = AdaptiveQuizInput(user_id="u1", question_bank=[])
        r = asyncio.run(adaptive_quiz_session(cfg, inp, tmp_path))
        assert r["status"] == "ok"
        assert r["questions"] == []

    def test_kc_distribution_present(self, tmp_path):
        cfg = AdaptiveQuizConfig(target_count=4)
        inp = AdaptiveQuizInput(user_id="u1", question_bank=self._bank())
        r = asyncio.run(adaptive_quiz_session(cfg, inp, tmp_path))
        assert "kc_distribution" in r

    def test_fingerprint_present(self, tmp_path):
        cfg = AdaptiveQuizConfig()
        inp = AdaptiveQuizInput(user_id="u1", session_id="s1")
        r = asyncio.run(adaptive_quiz_session(cfg, inp, tmp_path))
        assert "fingerprint" in r

    def test_mastery_threshold_filters(self, tmp_path):
        bank = [
            {"question_id": "q1", "kc_id": "A", "difficulty": 0.5, "mastery": 0.1},
            {"question_id": "q2", "kc_id": "B", "difficulty": 0.5, "mastery": 0.9},
        ]
        cfg = AdaptiveQuizConfig(mastery_threshold=0.8, target_count=2)
        inp = AdaptiveQuizInput(user_id="u1", question_bank=bank)
        r = asyncio.run(adaptive_quiz_session(cfg, inp, tmp_path))
        kc_ids = [q["kc_id"] for q in r["questions"]]
        assert "B" not in kc_ids

    def test_mastery_coverage_metric(self, tmp_path):
        cfg = AdaptiveQuizConfig(target_count=4)
        inp = AdaptiveQuizInput(user_id="u1", question_bank=self._bank())
        r = asyncio.run(adaptive_quiz_session(cfg, inp, tmp_path))
        assert 0.0 <= r["mastery_coverage"] <= 1.0

    def test_dropped_count(self, tmp_path):
        cfg = AdaptiveQuizConfig(target_count=2)
        inp = AdaptiveQuizInput(user_id="u1", question_bank=self._bank(8))
        r = asyncio.run(adaptive_quiz_session(cfg, inp, tmp_path))
        assert r["dropped_count"] >= 0

    def test_trail_written(self, tmp_path):
        cfg = AdaptiveQuizConfig()
        inp = AdaptiveQuizInput(user_id="u1")
        asyncio.run(adaptive_quiz_session(cfg, inp, tmp_path))
        assert len(list(tmp_path.glob("decision_trail_*.json"))) >= 1


# ---------------------------------------------------------------------------
# ME-3: socratic_tutor_session
# ---------------------------------------------------------------------------

class TestSocraticTutorSession:
    def test_status_ok(self, tmp_path):
        caller = make_caller("继续思考")
        cfg = SocraticTutorConfig()
        inp = SocraticTutorInput(question="x+1=5", correct_answer="4", student_messages=["不知道"])
        r = asyncio.run(socratic_tutor_session(cfg, inp, tmp_path, caller=caller))
        assert r["status"] == "ok"

    def test_turns_returned(self, tmp_path):
        caller = make_caller("想想等式两边")
        cfg = SocraticTutorConfig()
        inp = SocraticTutorInput(question="x=?", correct_answer="3", student_messages=["1", "2"])
        r = asyncio.run(socratic_tutor_session(cfg, inp, tmp_path, caller=caller))
        assert len(r["turns"]) == 2

    def test_answer_leakage_caught(self, tmp_path):
        answer = "7"
        caller = make_caller(f"答案是 {answer}")
        cfg = SocraticTutorConfig()
        inp = SocraticTutorInput(question="x=?", correct_answer=answer, student_messages=["不会"])
        r = asyncio.run(socratic_tutor_session(cfg, inp, tmp_path, caller=caller))
        assert r["violation_count"] >= 1
        for turn in r["turns"]:
            assert answer not in turn["assistant"]

    def test_empty_messages(self, tmp_path):
        caller = make_caller("ok")
        cfg = SocraticTutorConfig()
        inp = SocraticTutorInput(question="q", correct_answer="a", student_messages=[])
        r = asyncio.run(socratic_tutor_session(cfg, inp, tmp_path, caller=caller))
        assert r["status"] == "ok"
        assert r["turns"] == []

    def test_max_turns_respected(self, tmp_path):
        caller = make_caller("继续")
        cfg = SocraticTutorConfig(max_turns=3)
        inp = SocraticTutorInput(question="x", correct_answer="1", student_messages=["a"] * 10)
        r = asyncio.run(socratic_tutor_session(cfg, inp, tmp_path, caller=caller))
        assert len(r["turns"]) == 3

    def test_turn_count_tracked(self, tmp_path):
        caller = make_caller("ok")
        cfg = SocraticTutorConfig()
        inp = SocraticTutorInput(question="x", correct_answer="1", student_messages=["a", "b"])
        r = asyncio.run(socratic_tutor_session(cfg, inp, tmp_path, caller=caller))
        assert r["turn_count"] == 2

    def test_cost_tracked(self, tmp_path):
        caller = make_caller("ok")
        cfg = SocraticTutorConfig()
        inp = SocraticTutorInput(question="x", correct_answer="1", student_messages=["a"])
        r = asyncio.run(socratic_tutor_session(cfg, inp, tmp_path, caller=caller))
        assert "cost_usd" in r

    def test_fingerprint_present(self, tmp_path):
        caller = make_caller("ok")
        cfg = SocraticTutorConfig()
        inp = SocraticTutorInput(question="x", correct_answer="1")
        r = asyncio.run(socratic_tutor_session(cfg, inp, tmp_path, caller=caller))
        assert "fingerprint" in r

    def test_trail_written(self, tmp_path):
        caller = make_caller("ok")
        cfg = SocraticTutorConfig()
        inp = SocraticTutorInput(question="x", correct_answer="1")
        asyncio.run(socratic_tutor_session(cfg, inp, tmp_path, caller=caller))
        assert len(list(tmp_path.glob("decision_trail_*.json"))) >= 1

    def test_no_leakage_when_clean(self, tmp_path):
        caller = make_caller("想想这道题的思路")
        cfg = SocraticTutorConfig()
        inp = SocraticTutorInput(question="x+1=5", correct_answer="4", student_messages=["不知道"])
        r = asyncio.run(socratic_tutor_session(cfg, inp, tmp_path, caller=caller))
        assert r["violation_count"] == 0


# ---------------------------------------------------------------------------
# ME-4: grade_paper_workflow
# ---------------------------------------------------------------------------

class TestGradePaperWorkflow:
    def _make_inp(self, student_answer, expected_answer):
        return GradePaperInput(
            user_id="u1",
            paper_id="p1",
            questions=[
                PaperQuestion(question="x+1=3, x=?", student_answer=student_answer, expected_answer=expected_answer)
            ],
        )

    def test_correct_answer_grades_ok(self, tmp_path):
        caller = make_json_caller({"is_correct": True, "score": 1.0, "feedback": "ok"})
        cfg = GradePaperConfig()
        inp = self._make_inp("2", "2")
        r = asyncio.run(grade_paper_workflow(cfg, inp, tmp_path, caller=caller))
        assert r["status"] == "ok"

    def test_correct_count(self, tmp_path):
        caller = make_json_caller({"is_correct": True, "score": 1.0, "feedback": "ok"})
        cfg = GradePaperConfig()
        inp = GradePaperInput(
            questions=[
                PaperQuestion(question="q1", student_answer="2", expected_answer="2"),
                PaperQuestion(question="q2", student_answer="5", expected_answer="5"),
            ]
        )
        r = asyncio.run(grade_paper_workflow(cfg, inp, tmp_path, caller=caller))
        assert r["correct_count"] == 2

    def test_score_pct(self, tmp_path):
        caller = make_json_caller({"is_correct": True, "score": 1.0, "feedback": "ok"})
        cfg = GradePaperConfig()
        inp = self._make_inp("2", "2")
        r = asyncio.run(grade_paper_workflow(cfg, inp, tmp_path, caller=caller))
        assert 0.0 <= r["score_pct"] <= 100.0

    def test_report_written(self, tmp_path):
        caller = make_json_caller({"is_correct": True, "score": 1.0, "feedback": "ok"})
        cfg = GradePaperConfig()
        inp = self._make_inp("2", "2")
        r = asyncio.run(grade_paper_workflow(cfg, inp, tmp_path, caller=caller))
        if r.get("report_path"):
            assert Path(r["report_path"]).exists()

    def test_fingerprint_present(self, tmp_path):
        caller = make_json_caller({"is_correct": True, "score": 1.0, "feedback": "ok"})
        cfg = GradePaperConfig()
        r = asyncio.run(grade_paper_workflow(cfg, GradePaperInput(), tmp_path, caller=caller))
        assert "fingerprint" in r

    def test_empty_questions(self, tmp_path):
        caller = make_caller("ok")
        cfg = GradePaperConfig()
        r = asyncio.run(grade_paper_workflow(cfg, GradePaperInput(), tmp_path, caller=caller))
        assert r["status"] == "ok"
        assert r["correct_count"] == 0

    def test_grades_list_present(self, tmp_path):
        caller = make_json_caller({"is_correct": False, "score": 0.0, "feedback": "wrong"})
        cfg = GradePaperConfig()
        inp = self._make_inp("99", "2")
        r = asyncio.run(grade_paper_workflow(cfg, inp, tmp_path, caller=caller))
        assert isinstance(r["grades"], list)

    def test_trail_written(self, tmp_path):
        caller = make_caller("ok")
        cfg = GradePaperConfig()
        asyncio.run(grade_paper_workflow(cfg, GradePaperInput(), tmp_path, caller=caller))
        assert len(list(tmp_path.glob("decision_trail_*.json"))) >= 1

    def test_total_count(self, tmp_path):
        caller = make_json_caller({"is_correct": True, "score": 1.0, "feedback": "ok"})
        cfg = GradePaperConfig()
        inp = GradePaperInput(
            questions=[PaperQuestion(question="q", student_answer="1", expected_answer="1")] * 3
        )
        r = asyncio.run(grade_paper_workflow(cfg, inp, tmp_path, caller=caller))
        assert r["total_count"] == 3

    def test_on_step_called(self, tmp_path):
        steps = []
        caller = make_json_caller({"is_correct": True, "score": 1.0, "feedback": "ok"})
        cfg = GradePaperConfig()
        inp = self._make_inp("2", "2")
        asyncio.run(grade_paper_workflow(cfg, inp, tmp_path, caller=caller, on_step=lambda *a: steps.append(a)))
        assert len(steps) > 0


# ---------------------------------------------------------------------------
# ME-5: daily_mission_workflow (sync!)
# ---------------------------------------------------------------------------

class TestDailyMissionWorkflow:
    def _questions(self, n=10):
        kcs = ["algebra", "geometry", "calculus", "trig"]
        return [
            {"question_id": f"q{i}", "kc_id": kcs[i % 4], "difficulty": 0.5, "mastery": 0.3}
            for i in range(n)
        ]

    def test_is_sync(self, tmp_path):
        import inspect
        assert not inspect.iscoroutinefunction(daily_mission_workflow)

    def test_status_ok(self, tmp_path):
        cfg = DailyMissionConfig(mission_count=3)
        inp = DailyMissionInput(user_id="u1", available_questions=self._questions())
        r = daily_mission_workflow(cfg, inp, tmp_path)
        assert r["status"] == "ok"

    def test_mission_count(self, tmp_path):
        cfg = DailyMissionConfig(mission_count=4)
        inp = DailyMissionInput(user_id="u1", available_questions=self._questions())
        r = daily_mission_workflow(cfg, inp, tmp_path)
        assert len(r["missions"]) <= 4

    def test_empty_bank(self, tmp_path):
        cfg = DailyMissionConfig()
        inp = DailyMissionInput(user_id="u1", available_questions=[])
        r = daily_mission_workflow(cfg, inp, tmp_path)
        assert r["status"] == "ok"
        assert r["missions"] == []

    def test_priority_field_present(self, tmp_path):
        cfg = DailyMissionConfig(mission_count=3)
        inp = DailyMissionInput(user_id="u1", available_questions=self._questions())
        r = daily_mission_workflow(cfg, inp, tmp_path)
        for m in r["missions"]:
            assert "priority" in m

    def test_fingerprint_present(self, tmp_path):
        cfg = DailyMissionConfig()
        inp = DailyMissionInput(user_id="u1", mission_date="2026-06-14")
        r = daily_mission_workflow(cfg, inp, tmp_path)
        assert "fingerprint" in r

    def test_cost_zero(self, tmp_path):
        cfg = DailyMissionConfig()
        inp = DailyMissionInput(user_id="u1", available_questions=[])
        r = daily_mission_workflow(cfg, inp, tmp_path)
        assert r["cost_usd"] == 0.0

    def test_trail_written(self, tmp_path):
        cfg = DailyMissionConfig()
        inp = DailyMissionInput(user_id="u1")
        daily_mission_workflow(cfg, inp, tmp_path)
        assert len(list(tmp_path.glob("decision_trail_*.json"))) >= 1

    def test_on_step_called(self, tmp_path):
        steps = []
        cfg = DailyMissionConfig()
        inp = DailyMissionInput(user_id="u1")
        daily_mission_workflow(cfg, inp, tmp_path, on_step=lambda *a: steps.append(a))
        assert len(steps) > 0

    def test_mission_count_field(self, tmp_path):
        cfg = DailyMissionConfig(mission_count=3)
        inp = DailyMissionInput(user_id="u1", available_questions=self._questions())
        r = daily_mission_workflow(cfg, inp, tmp_path)
        assert r["mission_count"] == len(r["missions"])

    def test_mastery_map_respected(self, tmp_path):
        cfg = DailyMissionConfig(mission_count=2)
        inp = DailyMissionInput(
            user_id="u1",
            available_questions=[
                {"question_id": "q1", "kc_id": "A", "difficulty": 0.5},
                {"question_id": "q2", "kc_id": "B", "difficulty": 0.5},
            ],
            kc_mastery={"A": 0.1, "B": 0.9},
        )
        r = daily_mission_workflow(cfg, inp, tmp_path)
        assert r["status"] == "ok"


# ---------------------------------------------------------------------------
# ME-6: variant_generation_workflow
# ---------------------------------------------------------------------------

class TestVariantGenerationWorkflow:
    def test_status_ok(self, tmp_path):
        caller = make_json_caller({"question": "x+2=5, x=?", "kc_ids": ["algebra"]})
        cfg = VariantGenerationConfig(variants_per_question=1)
        inp = VariantGenerationInput(sources=[VariantSource(source_id="s1", question="x+1=3", kc_ids=["algebra"])])
        r = asyncio.run(variant_generation_workflow(cfg, inp, tmp_path, caller=caller))
        assert r["status"] == "ok"

    def test_variants_returned(self, tmp_path):
        caller = make_json_caller({"question": "x+2=5", "kc_ids": []})
        cfg = VariantGenerationConfig(variants_per_question=2)
        inp = VariantGenerationInput(sources=[VariantSource(question="x+1=3")])
        r = asyncio.run(variant_generation_workflow(cfg, inp, tmp_path, caller=caller))
        assert len(r["variants"]) == 2

    def test_answer_always_empty(self, tmp_path):
        caller = make_json_caller({"question": "new q", "answer": "SHOULD_NOT_APPEAR", "kc_ids": []})
        cfg = VariantGenerationConfig(variants_per_question=1)
        inp = VariantGenerationInput(sources=[VariantSource(question="q", answer="a")])
        r = asyncio.run(variant_generation_workflow(cfg, inp, tmp_path, caller=caller))
        for v in r["variants"]:
            assert v["answer"] == ""

    def test_kernel_verified_false(self, tmp_path):
        caller = make_json_caller({"question": "q", "kc_ids": []})
        cfg = VariantGenerationConfig(variants_per_question=1)
        inp = VariantGenerationInput(sources=[VariantSource(question="q")])
        r = asyncio.run(variant_generation_workflow(cfg, inp, tmp_path, caller=caller))
        for v in r["variants"]:
            assert v["kernel_verified"] is False

    def test_empty_sources(self, tmp_path):
        caller = make_caller("ok")
        cfg = VariantGenerationConfig()
        inp = VariantGenerationInput(sources=[])
        r = asyncio.run(variant_generation_workflow(cfg, inp, tmp_path, caller=caller))
        assert r["status"] == "ok"
        assert r["variants"] == []

    def test_total_count(self, tmp_path):
        caller = make_json_caller({"question": "q2", "kc_ids": []})
        cfg = VariantGenerationConfig(variants_per_question=3)
        inp = VariantGenerationInput(sources=[VariantSource(question="q")])
        r = asyncio.run(variant_generation_workflow(cfg, inp, tmp_path, caller=caller))
        assert r["total_count"] == 3

    def test_source_id_in_variant(self, tmp_path):
        caller = make_json_caller({"question": "q", "kc_ids": []})
        cfg = VariantGenerationConfig(variants_per_question=1)
        inp = VariantGenerationInput(sources=[VariantSource(source_id="myid", question="q")])
        r = asyncio.run(variant_generation_workflow(cfg, inp, tmp_path, caller=caller))
        assert r["variants"][0]["source_id"] == "myid"

    def test_fingerprint_present(self, tmp_path):
        caller = make_caller("ok")
        cfg = VariantGenerationConfig()
        inp = VariantGenerationInput(sources=[])
        r = asyncio.run(variant_generation_workflow(cfg, inp, tmp_path, caller=caller))
        assert "fingerprint" in r

    def test_trail_written(self, tmp_path):
        caller = make_json_caller({"question": "q", "kc_ids": []})
        cfg = VariantGenerationConfig(variants_per_question=1)
        inp = VariantGenerationInput(sources=[VariantSource(question="q")])
        asyncio.run(variant_generation_workflow(cfg, inp, tmp_path, caller=caller))
        assert len(list(tmp_path.glob("decision_trail_*.json"))) >= 1

    def test_multiple_sources(self, tmp_path):
        caller = make_json_caller({"question": "q", "kc_ids": []})
        cfg = VariantGenerationConfig(variants_per_question=1)
        inp = VariantGenerationInput(sources=[
            VariantSource(source_id="s1", question="q1"),
            VariantSource(source_id="s2", question="q2"),
        ])
        r = asyncio.run(variant_generation_workflow(cfg, inp, tmp_path, caller=caller))
        assert r["total_count"] == 2


# ---------------------------------------------------------------------------
# ME-7: learning_progress_report
# ---------------------------------------------------------------------------

class TestLearningProgressReport:
    def _attempts(self, kc_id, correct_seq, base_ts=0.0):
        return [
            {"question_id": f"{kc_id}_{i}", "kc_id": kc_id, "correct": c, "timestamp": base_ts + i * 3600}
            for i, c in enumerate(correct_seq)
        ]

    def test_status_ok(self, tmp_path):
        cfg = LearningProgressConfig()
        inp = ProgressInput(user_id="u1", attempt_records=self._attempts("A", [True, True, True]))
        r = asyncio.run(learning_progress_report(cfg, inp, tmp_path))
        assert r["status"] == "ok"

    def test_trajectories_returned(self, tmp_path):
        cfg = LearningProgressConfig(min_attempts_per_kc=3)
        inp = ProgressInput(user_id="u1", attempt_records=self._attempts("algebra", [False, True, True]))
        r = asyncio.run(learning_progress_report(cfg, inp, tmp_path))
        assert "trajectories" in r

    def test_overall_trend_float(self, tmp_path):
        cfg = LearningProgressConfig()
        inp = ProgressInput(user_id="u1", attempt_records=self._attempts("A", [True] * 4))
        r = asyncio.run(learning_progress_report(cfg, inp, tmp_path))
        assert isinstance(r["overall_trend"], float)

    def test_report_written(self, tmp_path):
        cfg = LearningProgressConfig()
        inp = ProgressInput(user_id="u1", attempt_records=[])
        r = asyncio.run(learning_progress_report(cfg, inp, tmp_path))
        if r.get("report_path"):
            assert Path(r["report_path"]).exists()

    def test_empty_records(self, tmp_path):
        cfg = LearningProgressConfig()
        inp = ProgressInput(user_id="u1", attempt_records=[])
        r = asyncio.run(learning_progress_report(cfg, inp, tmp_path))
        assert r["status"] == "ok"
        assert r["overall_trend"] == 0.0

    def test_sessions_analyzed(self, tmp_path):
        cfg = LearningProgressConfig()
        records = (
            self._attempts("A", [True, True, True], base_ts=0.0) +
            self._attempts("B", [False, True, True], base_ts=86400.0)
        )
        inp = ProgressInput(user_id="u1", attempt_records=records)
        r = asyncio.run(learning_progress_report(cfg, inp, tmp_path))
        assert r["sessions_analyzed"] >= 1

    def test_fingerprint_present(self, tmp_path):
        cfg = LearningProgressConfig()
        inp = ProgressInput(user_id="u1")
        r = asyncio.run(learning_progress_report(cfg, inp, tmp_path))
        assert "fingerprint" in r

    def test_improving_kcs_list(self, tmp_path):
        cfg = LearningProgressConfig()
        inp = ProgressInput(user_id="u1", attempt_records=[])
        r = asyncio.run(learning_progress_report(cfg, inp, tmp_path))
        assert isinstance(r["improving_kcs"], list)

    def test_trail_written(self, tmp_path):
        cfg = LearningProgressConfig()
        inp = ProgressInput(user_id="u1")
        asyncio.run(learning_progress_report(cfg, inp, tmp_path))
        assert len(list(tmp_path.glob("decision_trail_*.json"))) >= 1

    def test_on_step_called(self, tmp_path):
        steps = []
        cfg = LearningProgressConfig()
        inp = ProgressInput(user_id="u1")
        asyncio.run(learning_progress_report(cfg, inp, tmp_path, on_step=lambda *a: steps.append(a)))
        assert len(steps) > 0


# ---------------------------------------------------------------------------
# ME-8: breakpoint_remediation_workflow
# ---------------------------------------------------------------------------

class TestBreakpointRemediationWorkflow:
    def _wrong_q(self, qid="q1"):
        return WrongQuestionEntry(
            question_id=qid,
            question_text="x+1=5, x=?",
            student_answer="6",
            correct_answer="4",
            kc_ids=["algebra"],
            error_type="sign_error",
        )

    def test_status_ok(self, tmp_path):
        caller = make_json_caller({
            "breakpoints": ["符号错误"],
            "dominant_error_type": "sign_error",
            "affected_question_ids": ["q1"],
            "summary": "学生在移项时符号出错",
        })
        cfg = BreakpointRemediationConfig()
        inp = BreakpointRemediationInput(user_id="u1", wrong_questions=[self._wrong_q()])
        r = asyncio.run(breakpoint_remediation_workflow(cfg, inp, tmp_path, caller=caller))
        assert r["status"] == "ok"

    def test_empty_wrong_questions(self, tmp_path):
        caller = make_caller("ok")
        cfg = BreakpointRemediationConfig()
        inp = BreakpointRemediationInput(user_id="u1", wrong_questions=[])
        r = asyncio.run(breakpoint_remediation_workflow(cfg, inp, tmp_path, caller=caller))
        assert r["status"] == "ok"
        assert r["breakpoints"] == []

    def test_breakpoints_returned(self, tmp_path):
        caller = make_json_caller({
            "breakpoints": ["bp1", "bp2"],
            "dominant_error_type": "calc",
            "affected_question_ids": ["q1"],
            "summary": "summary",
        })
        cfg = BreakpointRemediationConfig()
        inp = BreakpointRemediationInput(wrong_questions=[self._wrong_q()])
        r = asyncio.run(breakpoint_remediation_workflow(cfg, inp, tmp_path, caller=caller))
        assert isinstance(r["breakpoints"], list)

    def test_remediation_plan_text(self, tmp_path):
        caller = make_json_caller({
            "breakpoints": ["x"],
            "dominant_error_type": "calc",
            "affected_question_ids": [],
            "summary": "s",
        })
        cfg = BreakpointRemediationConfig()
        inp = BreakpointRemediationInput(wrong_questions=[self._wrong_q()])
        r = asyncio.run(breakpoint_remediation_workflow(cfg, inp, tmp_path, caller=caller))
        assert isinstance(r["remediation_plan"], str)
        assert len(r["remediation_plan"]) > 0

    def test_report_written(self, tmp_path):
        caller = make_json_caller({
            "breakpoints": [],
            "dominant_error_type": "",
            "affected_question_ids": [],
            "summary": "none",
        })
        cfg = BreakpointRemediationConfig()
        inp = BreakpointRemediationInput(wrong_questions=[self._wrong_q()])
        r = asyncio.run(breakpoint_remediation_workflow(cfg, inp, tmp_path, caller=caller))
        if r.get("report_path"):
            assert Path(r["report_path"]).exists()

    def test_fingerprint_present(self, tmp_path):
        caller = make_json_caller({
            "breakpoints": [],
            "dominant_error_type": "",
            "affected_question_ids": [],
            "summary": "",
        })
        cfg = BreakpointRemediationConfig()
        inp = BreakpointRemediationInput(wrong_questions=[self._wrong_q()])
        r = asyncio.run(breakpoint_remediation_workflow(cfg, inp, tmp_path, caller=caller))
        assert "fingerprint" in r

    def test_trail_written(self, tmp_path):
        caller = make_json_caller({
            "breakpoints": [],
            "dominant_error_type": "",
            "affected_question_ids": [],
            "summary": "",
        })
        cfg = BreakpointRemediationConfig()
        inp = BreakpointRemediationInput(wrong_questions=[self._wrong_q()])
        asyncio.run(breakpoint_remediation_workflow(cfg, inp, tmp_path, caller=caller))
        assert len(list(tmp_path.glob("decision_trail_*.json"))) >= 1

    def test_empty_returns_no_llm_call(self, tmp_path):
        spy_called = []
        async def spy(**kwargs):
            spy_called.append(True)
            return {"content": [{"type": "text", "text": "{}"}], "stop_reason": "end_turn", "usage": {}}
        cfg = BreakpointRemediationConfig()
        inp = BreakpointRemediationInput(wrong_questions=[])
        asyncio.run(breakpoint_remediation_workflow(cfg, inp, tmp_path, caller=spy))
        assert len(spy_called) == 0

    def test_dominant_error_type(self, tmp_path):
        caller = make_json_caller({
            "breakpoints": [],
            "dominant_error_type": "arithmetic",
            "affected_question_ids": [],
            "summary": "arithmetic errors",
        })
        cfg = BreakpointRemediationConfig()
        inp = BreakpointRemediationInput(wrong_questions=[self._wrong_q()])
        r = asyncio.run(breakpoint_remediation_workflow(cfg, inp, tmp_path, caller=caller))
        assert r["dominant_error_type"] == "arithmetic"

    def test_multiple_wrong_questions(self, tmp_path):
        caller = make_json_caller({
            "breakpoints": ["b1", "b2"],
            "dominant_error_type": "calc",
            "affected_question_ids": ["q1", "q2"],
            "summary": "two errors",
        })
        cfg = BreakpointRemediationConfig()
        inp = BreakpointRemediationInput(
            wrong_questions=[self._wrong_q("q1"), self._wrong_q("q2")]
        )
        r = asyncio.run(breakpoint_remediation_workflow(cfg, inp, tmp_path, caller=caller))
        assert r["status"] == "ok"


# ---------------------------------------------------------------------------
# ME-9: user_data_workflow
# Mandatory: test_deleted_user_not_queryable
# ---------------------------------------------------------------------------

class TestUserDataWorkflow:
    def setup_method(self):
        reset_store()

    def _store(self):
        return {}

    def test_create_user(self, tmp_path):
        store = self._store()
        cfg = UserDataConfig()
        inp = UserDataInput(
            user_id="u1",
            operation="create",
            record=UserRecord(user_id="u1", display_name="Alice"),
        )
        r = asyncio.run(user_data_workflow(cfg, inp, tmp_path, store=store))
        assert r["status"] == "ok"
        assert r["record"]["display_name"] == "Alice"

    def test_query_existing_user(self, tmp_path):
        store = self._store()
        store["u1"] = UserRecord(user_id="u1", display_name="Bob")
        cfg = UserDataConfig()
        inp = UserDataInput(user_id="u1", operation="query")
        r = asyncio.run(user_data_workflow(cfg, inp, tmp_path, store=store))
        assert r["found"] is True
        assert r["record"]["display_name"] == "Bob"

    def test_query_missing_user(self, tmp_path):
        store = self._store()
        cfg = UserDataConfig()
        inp = UserDataInput(user_id="nonexistent", operation="query")
        r = asyncio.run(user_data_workflow(cfg, inp, tmp_path, store=store))
        assert r["found"] is False
        assert r["record"] is None

    def test_deleted_user_not_queryable(self, tmp_path):
        """Mandatory: after deletion, user must not be queryable."""
        store = self._store()
        store["u1"] = UserRecord(user_id="u1", display_name="ToDelete")
        cfg = UserDataConfig(soft_delete=True)

        # Delete
        del_inp = UserDataInput(user_id="u1", operation="delete")
        r_del = asyncio.run(user_data_workflow(cfg, del_inp, tmp_path, store=store))
        assert r_del["deleted"] is True

        # Query must return not found
        q_inp = UserDataInput(user_id="u1", operation="query")
        r_q = asyncio.run(user_data_workflow(cfg, q_inp, tmp_path, store=store))
        assert r_q["found"] is False
        assert r_q["record"] is None

    def test_hard_delete(self, tmp_path):
        store = self._store()
        store["u1"] = UserRecord(user_id="u1")
        cfg = UserDataConfig(soft_delete=False)
        del_inp = UserDataInput(user_id="u1", operation="delete")
        asyncio.run(user_data_workflow(cfg, del_inp, tmp_path, store=store))
        assert "u1" not in store

    def test_delete_nonexistent(self, tmp_path):
        store = self._store()
        cfg = UserDataConfig()
        inp = UserDataInput(user_id="ghost", operation="delete")
        r = asyncio.run(user_data_workflow(cfg, inp, tmp_path, store=store))
        assert r["status"] == "ok"
        assert r["deleted"] is False

    def test_fingerprint_present(self, tmp_path):
        store = self._store()
        cfg = UserDataConfig()
        inp = UserDataInput(
            user_id="u1", operation="create", record=UserRecord(user_id="u1")
        )
        r = asyncio.run(user_data_workflow(cfg, inp, tmp_path, store=store))
        assert "fingerprint" in r

    def test_trail_written(self, tmp_path):
        store = self._store()
        cfg = UserDataConfig()
        inp = UserDataInput(user_id="u1", operation="query")
        asyncio.run(user_data_workflow(cfg, inp, tmp_path, store=store))
        assert len(list(tmp_path.glob("decision_trail_*.json"))) >= 1

    def test_unknown_operation_errors(self, tmp_path):
        store = self._store()
        cfg = UserDataConfig()
        inp = UserDataInput(user_id="u1", operation="INVALID")
        r = asyncio.run(user_data_workflow(cfg, inp, tmp_path, store=store))
        assert r["status"] == "error"

    def test_create_sets_created_at(self, tmp_path):
        store = self._store()
        cfg = UserDataConfig()
        inp = UserDataInput(
            user_id="u1", operation="create", record=UserRecord(user_id="u1")
        )
        r = asyncio.run(user_data_workflow(cfg, inp, tmp_path, store=store))
        assert r["record"]["created_at"] > 0

    def test_on_step_called(self, tmp_path):
        steps = []
        store = self._store()
        cfg = UserDataConfig()
        inp = UserDataInput(
            user_id="u1", operation="create", record=UserRecord(user_id="u1")
        )
        asyncio.run(user_data_workflow(cfg, inp, tmp_path, store=store, on_step=lambda *a: steps.append(a)))
        assert len(steps) > 0
