"""Tests for M-G1: conflict_detection_workflow."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from omodul.conflict_detection_workflow import (
    ConflictDetectionConfig,
    conflict_detection_workflow,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(**overrides) -> ConflictDetectionConfig:
    base = dict(
        corpus_id="corpus_001",
        batch_id="batch_42",
        llm_provider="mock",
        llm_model="mock-model",
    )
    base.update(overrides)
    return ConflictDetectionConfig(**base)


def _make_input(
    n_new: int = 2,
    n_existing: int = 2,
    *,
    new_texts=None,
    new_embs=None,
    exist_texts=None,
    exist_embs=None,
    exist_ids=None,
):
    from oprim._aii_graph_types import ConflictDetectionInput
    return ConflictDetectionInput(
        new_ku_texts=new_texts or [f"new_text_{i}" for i in range(n_new)],
        new_ku_embeddings=new_embs or [[float(i), 0.0] for i in range(n_new)],
        existing_ku_texts=exist_texts or [f"exist_text_{i}" for i in range(n_existing)],
        existing_ku_embeddings=exist_embs or [[float(i) * 0.1, 0.0] for i in range(n_existing)],
        existing_ku_ids=exist_ids or [f"ku_{i}" for i in range(n_existing)],
    )


def _llm_response(payload):
    text = json.dumps(payload, ensure_ascii=False)
    async def llm(*, messages, system=None, max_tokens=256, **kw):
        return {"content": [{"type": "text", "text": text}], "usage": {}}
    return llm


def _mock_registry(llm_fn):
    registry = MagicMock()
    registry.llm = MagicMock(return_value=llm_fn)
    return registry


def _patch_registry(llm_fn):
    registry = _mock_registry(llm_fn)
    return patch("omodul.conflict_detection_workflow.ProviderRegistry.get", return_value=registry)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestConflictDetectionWorkflow:
    async def test_completed_status_on_success(self, tmp_path):
        llm = _llm_response(None)
        with _patch_registry(llm):
            result = await conflict_detection_workflow(
                config=_make_config(),
                input_data=_make_input(),
                output_dir=tmp_path,
            )
        assert result["status"] == "completed"

    async def test_conflict_pairs_in_result(self, tmp_path):
        # High-similarity opposing texts → conflict detected
        llm = _llm_response({
            "conflict_type": "factual_contradiction",
            "description": "contradiction",
            "severity": "high",
        })
        inp = _make_input(
            n_new=1, n_existing=1,
            new_texts=["该药物增加血压"],
            new_embs=[[1.0, 0.0]],
            exist_texts=["该药物减少血压"],
            exist_embs=[[0.999, 0.001]],
            exist_ids=["ku_exist_1"],
        )
        with _patch_registry(llm):
            result = await conflict_detection_workflow(
                config=_make_config(),
                input_data=inp,
                output_dir=tmp_path,
            )
        assert result["status"] == "completed"
        assert "conflict_pairs" in result
        assert isinstance(result["conflict_pairs"], list)

    async def test_no_conflicts_returns_empty_list(self, tmp_path):
        llm = _llm_response(None)
        inp = _make_input(
            new_texts=["machine learning"],
            new_embs=[[1.0, 0.0]],
            exist_texts=["quantum physics"],
            exist_embs=[[0.0, 1.0]],
            exist_ids=["ku_qp"],
        )
        with _patch_registry(llm):
            result = await conflict_detection_workflow(
                config=_make_config(),
                input_data=inp,
                output_dir=tmp_path,
            )
        assert result["status"] == "completed"
        assert result["conflict_pairs"] == []
        assert result["conflicts_found"] == 0

    async def test_empty_new_texts_returns_failed(self, tmp_path):
        llm = _llm_response(None)
        inp = _make_input(n_new=0, n_existing=1)
        with _patch_registry(llm):
            result = await conflict_detection_workflow(
                config=_make_config(),
                input_data=inp,
                output_dir=tmp_path,
            )
        assert result["status"] == "failed"
        assert "empty" in result["error"]["message"].lower()

    async def test_fingerprint_in_result(self, tmp_path):
        llm = _llm_response(None)
        with _patch_registry(llm):
            result = await conflict_detection_workflow(
                config=_make_config(),
                input_data=_make_input(),
                output_dir=tmp_path,
            )
        assert "fingerprint" in result
        assert isinstance(result["fingerprint"], str)

    async def test_corpus_id_batch_id_in_result(self, tmp_path):
        llm = _llm_response(None)
        with _patch_registry(llm):
            result = await conflict_detection_workflow(
                config=_make_config(corpus_id="c42", batch_id="b99"),
                input_data=_make_input(),
                output_dir=tmp_path,
            )
        assert result["corpus_id"] == "c42"
        assert result["batch_id"] == "b99"

    async def test_conflict_grade_always_unverified(self, tmp_path):
        llm = _llm_response({
            "conflict_type": "factual_contradiction",
            "description": "x",
            "severity": "high",
        })
        inp = _make_input(
            new_texts=["支持该政策"],
            new_embs=[[1.0, 0.0]],
            exist_texts=["反对该政策"],
            exist_embs=[[0.999, 0.001]],
            exist_ids=["ku_policy"],
        )
        with _patch_registry(llm):
            result = await conflict_detection_workflow(
                config=_make_config(),
                input_data=inp,
                output_dir=tmp_path,
            )
        for pair in result.get("conflict_pairs", []):
            assert pair["grade"] == "unverified"

    async def test_on_step_callback_called(self, tmp_path):
        llm = _llm_response(None)
        steps = []

        def on_step(step, state):
            steps.append((step, state))

        with _patch_registry(llm):
            result = await conflict_detection_workflow(
                config=_make_config(),
                input_data=_make_input(),
                output_dir=tmp_path,
                on_step=on_step,
            )
        assert result["status"] == "completed"
        assert any("conflict_detection" in s[0] for s in steps)

    async def test_trail_written_to_output_dir(self, tmp_path):
        llm = _llm_response(None)
        with _patch_registry(llm):
            result = await conflict_detection_workflow(
                config=_make_config(),
                input_data=_make_input(),
                output_dir=tmp_path,
            )
        assert result["status"] == "completed"
        # trail_path either None or exists in tmp_path
        if result.get("trail_path"):
            assert Path(result["trail_path"]).exists()

    async def test_multiple_new_kus_checked(self, tmp_path):
        llm = _llm_response(None)
        inp = _make_input(n_new=3, n_existing=2)
        with _patch_registry(llm):
            result = await conflict_detection_workflow(
                config=_make_config(),
                input_data=inp,
                output_dir=tmp_path,
            )
        assert result["status"] == "completed"
        assert "conflicts_found" in result
