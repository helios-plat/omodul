"""Tests for M-animation_workflow."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from oprim._animation_types import AnimationInput, AnimationResult
from omodul.animation_workflow import AnimationConfig, animation_workflow


def _config(**kwargs):
    defaults = {"entity_id": "ent001", "domain": "physics"}
    defaults.update(kwargs)
    return AnimationConfig(**defaults)


def _input(**kwargs):
    defaults = {
        "template": "Generate: {topic}",
        "variables": {"topic": "gravity"},
        "domain_prompt": "Make it educational",
    }
    defaults.update(kwargs)
    return AnimationInput(**defaults)


def _good_anim(**kwargs):
    defaults = {
        "html": "<html><body>anim</body></html>",
        "is_valid": True,
        "validation_violations": [],
        "entity_meta": {"variables": {}, "domain_prompt_preview": ""},
    }
    defaults.update(kwargs)
    return AnimationResult(**defaults)


class TestAnimationWorkflow:

    @patch('omodul.animation_workflow.generate_animation', new_callable=AsyncMock)
    async def test_returns_completed_status(self, mock_gen, tmp_path):
        mock_gen.return_value = _good_anim()
        result = await animation_workflow(
            config=_config(), input_data=_input(), output_dir=tmp_path, llm=None,
        )
        assert result["status"] == "completed"

    @patch('omodul.animation_workflow.generate_animation', new_callable=AsyncMock)
    async def test_html_in_result(self, mock_gen, tmp_path):
        mock_gen.return_value = _good_anim(html="<html>MY</html>")
        result = await animation_workflow(
            config=_config(), input_data=_input(), output_dir=tmp_path, llm=None,
        )
        assert result["html"] == "<html>MY</html>"

    @patch('omodul.animation_workflow.generate_animation', new_callable=AsyncMock)
    async def test_is_valid_in_result(self, mock_gen, tmp_path):
        mock_gen.return_value = _good_anim(is_valid=True)
        result = await animation_workflow(
            config=_config(), input_data=_input(), output_dir=tmp_path, llm=None,
        )
        assert result["is_valid"] is True

    @patch('omodul.animation_workflow.generate_animation', new_callable=AsyncMock)
    async def test_fingerprint_in_result(self, mock_gen, tmp_path):
        mock_gen.return_value = _good_anim()
        result = await animation_workflow(
            config=_config(), input_data=_input(), output_dir=tmp_path, llm=None,
        )
        assert "fingerprint" in result
        assert result["fingerprint"]

    @patch('omodul.animation_workflow.generate_animation', new_callable=AsyncMock)
    async def test_entity_id_and_domain_in_result(self, mock_gen, tmp_path):
        mock_gen.return_value = _good_anim()
        result = await animation_workflow(
            config=_config(entity_id="ent999", domain="biology"),
            input_data=_input(),
            output_dir=tmp_path,
            llm=None,
        )
        assert result["entity_id"] == "ent999"
        assert result["domain"] == "biology"

    @patch('omodul.animation_workflow.generate_animation', new_callable=AsyncMock)
    async def test_decision_trail_written(self, mock_gen, tmp_path):
        mock_gen.return_value = _good_anim()
        result = await animation_workflow(
            config=_config(), input_data=_input(), output_dir=tmp_path, llm=None,
        )
        from pathlib import Path
        trail_info = result.get("decision_trail") or {}
        trail_path = trail_info.get("path") or result.get("trail_path")
        assert trail_path is not None
        assert Path(trail_path).exists()

    @patch('omodul.animation_workflow.generate_animation', new_callable=AsyncMock)
    async def test_db_writer_called_when_injected(self, mock_gen, tmp_path):
        mock_gen.return_value = _good_anim()
        db_writer = MagicMock()
        await animation_workflow(
            config=_config(), input_data=_input(), output_dir=tmp_path,
            llm=None, db_writer=db_writer,
        )
        db_writer.assert_called_once()
        call_arg = db_writer.call_args[0][0]
        assert call_arg["entity_id"] == "ent001"
        assert "html" in call_arg

    @patch('omodul.animation_workflow.generate_animation', new_callable=AsyncMock)
    async def test_db_writer_none_no_error(self, mock_gen, tmp_path):
        mock_gen.return_value = _good_anim()
        result = await animation_workflow(
            config=_config(), input_data=_input(), output_dir=tmp_path,
            llm=None, db_writer=None,
        )
        assert result["status"] == "completed"

    @patch('omodul.animation_workflow.generate_animation', new_callable=AsyncMock)
    async def test_on_step_callback_called(self, mock_gen, tmp_path):
        mock_gen.return_value = _good_anim()
        steps = []
        def on_step(step, state):
            steps.append((step, state))
        await animation_workflow(
            config=_config(), input_data=_input(), output_dir=tmp_path,
            llm=None, on_step=on_step,
        )
        assert any("animation_workflow" in s[0] for s in steps)

    @patch('omodul.animation_workflow.generate_animation', new_callable=AsyncMock)
    async def test_generation_error_returns_failed_no_raise(self, mock_gen, tmp_path):
        mock_gen.side_effect = RuntimeError("LLM down")
        result = await animation_workflow(
            config=_config(), input_data=_input(), output_dir=tmp_path, llm=None,
        )
        assert result["status"] == "failed"
        assert result["html"] is None

    @patch('omodul.animation_workflow.generate_animation', new_callable=AsyncMock)
    async def test_validation_failure_still_completes(self, mock_gen, tmp_path):
        # is_valid=False should not cause status=failed — it's metadata, not an error
        mock_gen.return_value = _good_anim(
            is_valid=False,
            validation_violations=["inline_event_handler"],
        )
        result = await animation_workflow(
            config=_config(), input_data=_input(), output_dir=tmp_path, llm=None,
        )
        assert result["status"] == "completed"
        assert result["is_valid"] is False
        assert "inline_event_handler" in result["validation_violations"]

    @patch('omodul.animation_workflow.generate_animation', new_callable=AsyncMock)
    async def test_db_writer_exception_does_not_fail_workflow(self, mock_gen, tmp_path):
        mock_gen.return_value = _good_anim()
        def bad_writer(data):
            raise ConnectionError("DB offline")
        result = await animation_workflow(
            config=_config(), input_data=_input(), output_dir=tmp_path,
            llm=None, db_writer=bad_writer,
        )
        # Workflow still completes; DB failure is trail-recorded, not propagated
        assert result["status"] == "completed"

    @patch('omodul.animation_workflow.generate_animation', new_callable=AsyncMock)
    async def test_fingerprint_deterministic_for_same_config(self, mock_gen, tmp_path):
        mock_gen.return_value = _good_anim()
        cfg = _config(entity_id="e1", domain="math")
        r1 = await animation_workflow(config=cfg, input_data=_input(), output_dir=tmp_path, llm=None)
        r2 = await animation_workflow(config=cfg, input_data=_input(), output_dir=tmp_path, llm=None)
        assert r1["fingerprint"] == r2["fingerprint"]
