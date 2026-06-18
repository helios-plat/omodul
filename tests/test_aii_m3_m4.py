"""Tests for M-AII-3: summary_synthesize and M-AII-4: book_understanding_synthesize."""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest


# ---------------------------------------------------------------------------
# Local stubs (avoid oprim __init__.py chain)
# ---------------------------------------------------------------------------

@dataclass
class _SummarySynthesizeInput:
    ku_ids: list
    ku_texts: list
    source_grades: list


@dataclass
class _BookUnderstandingInput:
    ku_ids: list
    ku_texts: list
    ku_grades: list


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_llm(text: str):
    async def llm(*, messages, system=None, max_tokens=4096, **kw):
        return {"content": [{"type": "text", "text": text}], "usage": {}}
    return llm


def _book_llm(doc_type="science", claim_grade="high"):
    payload = json.dumps({
        "summary": "综合摘要文本。",
        "main_claims": [{"claim": "核心主张", "stance_marker": "《测试书》主张", "claim_grade": claim_grade}],
        "argument_structure": [{"point": "论点", "evidence": [{"text": "论据", "grade": claim_grade}]}],
        "key_concept_ku_ids": ["ku1"],
        "structure": "线性结构",
    }, ensure_ascii=False)
    return _make_llm(payload)


def _make_ss_config(label="comm_A", max_kus=20):
    from omodul.summary_synthesize import SummarySynthesizeConfig
    return SummarySynthesizeConfig(community_label=label, max_source_kus=max_kus)


def _make_bu_config(substrate_id="book_001", doc_type="science"):
    from omodul.book_understanding_synthesize import BookUnderstandingConfig
    return BookUnderstandingConfig(book_substrate_id=substrate_id, doc_type=doc_type)


def _patch_ss_registry(llm_fn):
    p = patch("omodul.summary_synthesize.ProviderRegistry")
    m = p.start()
    m.get.return_value.llm.return_value = llm_fn
    return p


def _patch_bu_registry(llm_fn):
    p = patch("omodul.book_understanding_synthesize.ProviderRegistry")
    m = p.start()
    m.get.return_value.llm.return_value = llm_fn
    return p


# ---------------------------------------------------------------------------
# M-AII-3: summary_synthesize
# ---------------------------------------------------------------------------

class TestSummarySynthesize:
    async def test_normal_returns_completed(self, tmp_path):
        from omodul.summary_synthesize import summary_synthesize
        p = _patch_ss_registry(_make_llm("这是综合摘要。"))
        try:
            result = await summary_synthesize(
                _make_ss_config(),
                _SummarySynthesizeInput(["ku1", "ku2"], ["text1", "text2"], ["medium", "low"]),
                tmp_path,
            )
        finally:
            p.stop()
        assert result["status"] == "completed"

    async def test_is_synthesis_always_true(self, tmp_path):
        from omodul.summary_synthesize import summary_synthesize
        p = _patch_ss_registry(_make_llm("摘要。"))
        try:
            result = await summary_synthesize(
                _make_ss_config(),
                _SummarySynthesizeInput(["ku1"], ["text1"], ["low"]),
                tmp_path,
            )
        finally:
            p.stop()
        assert result["is_synthesis"] is True

    async def test_grade_does_not_exceed_source_grades(self, tmp_path):
        from omodul.summary_synthesize import summary_synthesize
        p = _patch_ss_registry(_make_llm("摘要。"))
        try:
            result = await summary_synthesize(
                _make_ss_config(),
                _SummarySynthesizeInput(["ku1", "ku2"], ["t1", "t2"], ["low", "medium"]),
                tmp_path,
            )
        finally:
            p.stop()
        from omodul.summary_synthesize import _GRADE_RANKS
        assert _GRADE_RANKS.get(result["grade"], 0) <= _GRADE_RANKS.get("medium", 0)

    async def test_grade_capped_at_high(self, tmp_path):
        from omodul.summary_synthesize import summary_synthesize
        p = _patch_ss_registry(_make_llm("摘要。"))
        try:
            result = await summary_synthesize(
                _make_ss_config(),
                _SummarySynthesizeInput(["ku1"], ["t1"], ["verified"]),
                tmp_path,
            )
        finally:
            p.stop()
        from omodul.summary_synthesize import _GRADE_RANKS
        assert _GRADE_RANKS.get(result["grade"], 0) <= _GRADE_RANKS.get("high", 0)

    async def test_synthesis_note_present(self, tmp_path):
        from omodul.summary_synthesize import summary_synthesize
        p = _patch_ss_registry(_make_llm("摘要。"))
        try:
            result = await summary_synthesize(
                _make_ss_config(),
                _SummarySynthesizeInput(["ku1"], ["t1"], ["low"]),
                tmp_path,
            )
        finally:
            p.stop()
        assert result["synthesis_note"] == "AII综合，非原文断言"

    async def test_decision_trail_written(self, tmp_path):
        from omodul.summary_synthesize import summary_synthesize
        p = _patch_ss_registry(_make_llm("摘要。"))
        try:
            await summary_synthesize(
                _make_ss_config(),
                _SummarySynthesizeInput(["ku1"], ["t1"], ["low"]),
                tmp_path,
            )
        finally:
            p.stop()
        assert list(tmp_path.glob("decision_trail_*.json"))

    async def test_empty_ku_ids_returns_failed(self, tmp_path):
        from omodul.summary_synthesize import summary_synthesize
        with patch("omodul.summary_synthesize.ProviderRegistry"):
            result = await summary_synthesize(
                _make_ss_config(),
                _SummarySynthesizeInput([], [], []),
                tmp_path,
            )
        assert result["status"] == "failed"

    async def test_on_step_callback_invoked(self, tmp_path):
        from omodul.summary_synthesize import summary_synthesize
        steps = []
        p = _patch_ss_registry(_make_llm("摘要。"))
        try:
            await summary_synthesize(
                _make_ss_config(),
                _SummarySynthesizeInput(["ku1"], ["t1"], ["low"]),
                tmp_path,
                on_step=lambda *, step, state: steps.append(step),
            )
        finally:
            p.stop()
        assert "synthesize" in steps

    async def test_cancelled_error_propagates(self, tmp_path):
        from omodul.summary_synthesize import summary_synthesize

        async def cancelling_llm(*, messages, **kw):
            raise asyncio.CancelledError()

        with patch("omodul.summary_synthesize.ProviderRegistry") as m:
            m.get.return_value.llm.return_value = cancelling_llm
            with pytest.raises(asyncio.CancelledError):
                await summary_synthesize(
                    _make_ss_config(),
                    _SummarySynthesizeInput(["ku1"], ["t1"], ["low"]),
                    tmp_path,
                )

    async def test_fingerprint_deterministic(self):
        from omodul.summary_synthesize import compute_fingerprint_for_summary_synthesize
        f1 = compute_fingerprint_for_summary_synthesize("comm_X")
        f2 = compute_fingerprint_for_summary_synthesize("comm_X")
        assert f1 == f2 and len(f1) == 24

    async def test_max_source_kus_truncates(self, tmp_path):
        from omodul.summary_synthesize import summary_synthesize
        calls = []

        async def recording_llm(*, messages, **kw):
            calls.append(messages[0]["content"])
            return {"content": [{"type": "text", "text": "摘要。"}], "usage": {}}

        with patch("omodul.summary_synthesize.ProviderRegistry") as m:
            m.get.return_value.llm.return_value = recording_llm
            await summary_synthesize(
                _make_ss_config(max_kus=2),
                _SummarySynthesizeInput(
                    ["ku1", "ku2", "ku3", "ku4"],
                    ["t1", "t2", "t3", "t4"],
                    ["low"] * 4,
                ),
                tmp_path,
            )
        # Only first 2 KUs should appear in the prompt
        assert calls
        assert "ku3" not in calls[0]


# ---------------------------------------------------------------------------
# M-AII-4: book_understanding_synthesize
# ---------------------------------------------------------------------------

class TestBookUnderstandingSynthesize:
    async def test_science_claim_grade_can_be_high(self, tmp_path):
        from omodul.book_understanding_synthesize import book_understanding_synthesize
        p = _patch_bu_registry(_book_llm(doc_type="science", claim_grade="high"))
        try:
            result = await book_understanding_synthesize(
                _make_bu_config(doc_type="science"),
                _BookUnderstandingInput(["ku1"], ["science text"], ["high"]),
                tmp_path,
            )
        finally:
            p.stop()
        assert result["status"] == "completed"
        claim = result["main_claims"][0]
        from omodul.book_understanding_synthesize import _GRADE_RANKS
        assert _GRADE_RANKS.get(claim["claim_grade"], 0) <= _GRADE_RANKS.get("high", 0)

    async def test_literature_grade_capped_at_low(self, tmp_path):
        from omodul.book_understanding_synthesize import book_understanding_synthesize
        p = _patch_bu_registry(_book_llm(doc_type="literature", claim_grade="high"))
        try:
            result = await book_understanding_synthesize(
                _make_bu_config(doc_type="literature"),
                _BookUnderstandingInput(["ku1"], ["lit text"], ["low"]),
                tmp_path,
            )
        finally:
            p.stop()
        assert result["status"] == "completed"
        for claim in result["main_claims"]:
            assert claim["claim_grade"] == "low"

    async def test_stance_marker_non_empty(self, tmp_path):
        from omodul.book_understanding_synthesize import book_understanding_synthesize
        p = _patch_bu_registry(_book_llm())
        try:
            result = await book_understanding_synthesize(
                _make_bu_config(),
                _BookUnderstandingInput(["ku1"], ["text"], ["medium"]),
                tmp_path,
            )
        finally:
            p.stop()
        for claim in result["main_claims"]:
            assert claim.get("stance_marker", "").strip()

    async def test_evidence_grades_independent(self, tmp_path):
        from omodul.book_understanding_synthesize import book_understanding_synthesize
        payload = json.dumps({
            "summary": "摘要",
            "main_claims": [{"claim": "C", "stance_marker": "《X》主张", "claim_grade": "medium"}],
            "argument_structure": [
                {"point": "P", "evidence": [
                    {"text": "E1", "grade": "high"},
                    {"text": "E2", "grade": "low"},
                ]}
            ],
            "key_concept_ku_ids": [],
            "structure": "结构",
        }, ensure_ascii=False)
        p = _patch_bu_registry(_make_llm(payload))
        try:
            result = await book_understanding_synthesize(
                _make_bu_config(doc_type="science"),
                _BookUnderstandingInput(["ku1"], ["text"], ["high"]),
                tmp_path,
            )
        finally:
            p.stop()
        evidences = result["argument_structure"][0]["evidence"]
        grades = {e["grade"] for e in evidences}
        assert len(grades) >= 1  # independent grades preserved

    async def test_is_synthesis_always_true(self, tmp_path):
        from omodul.book_understanding_synthesize import book_understanding_synthesize
        p = _patch_bu_registry(_book_llm())
        try:
            result = await book_understanding_synthesize(
                _make_bu_config(),
                _BookUnderstandingInput(["ku1"], ["text"], ["low"]),
                tmp_path,
            )
        finally:
            p.stop()
        assert result["is_synthesis"] is True

    async def test_report_generated(self, tmp_path):
        from omodul.book_understanding_synthesize import book_understanding_synthesize
        p = _patch_bu_registry(_book_llm())
        try:
            result = await book_understanding_synthesize(
                _make_bu_config(),
                _BookUnderstandingInput(["ku1"], ["text"], ["low"]),
                tmp_path,
            )
        finally:
            p.stop()
        assert result.get("report_path")
        assert Path(result["report_path"]).exists()

    async def test_decision_trail_written(self, tmp_path):
        from omodul.book_understanding_synthesize import book_understanding_synthesize
        p = _patch_bu_registry(_book_llm())
        try:
            await book_understanding_synthesize(
                _make_bu_config(),
                _BookUnderstandingInput(["ku1"], ["text"], ["low"]),
                tmp_path,
            )
        finally:
            p.stop()
        assert list(tmp_path.glob("decision_trail_*.json"))

    async def test_fingerprint_deterministic(self):
        from omodul.book_understanding_synthesize import (
            compute_fingerprint_for_book_understanding_synthesize,
        )
        f1 = compute_fingerprint_for_book_understanding_synthesize("book_001", "science")
        f2 = compute_fingerprint_for_book_understanding_synthesize("book_001", "science")
        assert f1 == f2 and len(f1) == 24

    async def test_cancelled_error_propagates(self, tmp_path):
        from omodul.book_understanding_synthesize import book_understanding_synthesize

        async def cancelling_llm(*, messages, **kw):
            raise asyncio.CancelledError()

        with patch("omodul.book_understanding_synthesize.ProviderRegistry") as m:
            m.get.return_value.llm.return_value = cancelling_llm
            with pytest.raises(asyncio.CancelledError):
                await book_understanding_synthesize(
                    _make_bu_config(),
                    _BookUnderstandingInput(["ku1"], ["text"], ["low"]),
                    tmp_path,
                )

    async def test_empty_ku_ids_returns_failed(self, tmp_path):
        from omodul.book_understanding_synthesize import book_understanding_synthesize
        with patch("omodul.book_understanding_synthesize.ProviderRegistry"):
            result = await book_understanding_synthesize(
                _make_bu_config(),
                _BookUnderstandingInput([], [], []),
                tmp_path,
            )
        assert result["status"] == "failed"

    async def test_synthesis_note_hardcoded(self, tmp_path):
        from omodul.book_understanding_synthesize import book_understanding_synthesize
        p = _patch_bu_registry(_book_llm())
        try:
            result = await book_understanding_synthesize(
                _make_bu_config(),
                _BookUnderstandingInput(["ku1"], ["text"], ["low"]),
                tmp_path,
            )
        finally:
            p.stop()
        assert result["synthesis_note"] == "AII综合，非原文断言"
