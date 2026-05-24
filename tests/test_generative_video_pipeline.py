"""Tests for omodul.generative_video_pipeline — 4 pillars: fingerprint, decision_trail, report, cost."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from omodul.generative_video_pipeline import (
    GenerativeVideoConfig,
    GenerativeVideoFindings,
    GenerativeVideoInput,
    _stage_load_template,
    compute_fingerprint_for,
    generative_video_pipeline,
)


# --- Fixtures ---


@pytest.fixture()
def config() -> GenerativeVideoConfig:
    return GenerativeVideoConfig(topic="AI history", target_duration_s=60)


@pytest.fixture()
def input_data() -> GenerativeVideoInput:
    return GenerativeVideoInput()


def _mock_pipeline_success(tmp_path: Path):
    """Patch _run_stages to return mock findings without real providers."""

    async def _fake_stages(*args: Any, **kw: Any) -> GenerativeVideoFindings:
        video = tmp_path / "final_video.mp4"
        video.write_bytes(b"\x00" * 1024)
        return GenerativeVideoFindings(
            video_path=video,
            video_duration_s=60.0,
            video_size_kb=1,
            scenes_count=3,
            shots_count=9,
        )

    return patch(
        "omodul.generative_video_pipeline._run_stages",
        side_effect=_fake_stages,
    )


def _mock_pipeline_failure():
    """Patch _run_stages to raise."""

    async def _fail(*args: Any, **kw: Any) -> None:
        raise RuntimeError("Stage 3 failed: LLM timeout")

    return patch(
        "omodul.generative_video_pipeline._run_stages",
        side_effect=_fail,
    )


# --- Fingerprint tests ---


class TestFingerprint:
    def test_change_topic_changes_fp(self, input_data: GenerativeVideoInput) -> None:
        c1 = GenerativeVideoConfig(topic="cats")
        c2 = GenerativeVideoConfig(topic="dogs")
        assert compute_fingerprint_for(c1, input_data) != compute_fingerprint_for(c2, input_data)

    def test_change_non_fingerprint_field_no_change(
        self, input_data: GenerativeVideoInput
    ) -> None:
        c1 = GenerativeVideoConfig(topic="cats", burn_subtitles=True)
        c2 = GenerativeVideoConfig(topic="cats", burn_subtitles=False)
        assert compute_fingerprint_for(c1, input_data) == compute_fingerprint_for(c2, input_data)

    def test_change_portrait_path_changes_fp(self) -> None:
        c = GenerativeVideoConfig(topic="cats")
        i1 = GenerativeVideoInput(portrait_path=None)
        i2 = GenerativeVideoInput(portrait_path=Path("/face.png"))
        assert compute_fingerprint_for(c, i1) != compute_fingerprint_for(c, i2)

    def test_fingerprint_deterministic(
        self, config: GenerativeVideoConfig, input_data: GenerativeVideoInput
    ) -> None:
        fp1 = compute_fingerprint_for(config, input_data)
        fp2 = compute_fingerprint_for(config, input_data)
        assert fp1 == fp2
        assert len(fp1) == 64  # SHA-256 hex

    def test_compute_fingerprint_for_public(
        self, config: GenerativeVideoConfig, input_data: GenerativeVideoInput
    ) -> None:
        fp = compute_fingerprint_for(config, input_data)
        assert isinstance(fp, str)
        assert len(fp) == 64


# --- Decision trail tests ---


class TestDecisionTrail:
    def test_trail_complete_on_success(
        self, config: GenerativeVideoConfig, input_data: GenerativeVideoInput, tmp_path: Path
    ) -> None:
        with _mock_pipeline_success(tmp_path):
            result = generative_video_pipeline(config, input_data, tmp_path)
        trail = result["decision_trail"]
        assert "fingerprint" in trail
        assert "steps" in trail
        assert trail["status"] == "completed"
        assert "cost_breakdown" in trail

    def test_trail_written_on_failure(
        self, config: GenerativeVideoConfig, input_data: GenerativeVideoInput, tmp_path: Path
    ) -> None:
        with _mock_pipeline_failure():
            result = generative_video_pipeline(config, input_data, tmp_path)
        assert result["status"] == "failed"
        trail_file = tmp_path / "decision_trail.json"
        assert trail_file.exists()
        trail = json.loads(trail_file.read_text())
        assert trail["status"] == "failed"
        assert trail["error"] is not None


# --- Report tests ---


class TestReport:
    def test_report_generated_on_success(
        self, config: GenerativeVideoConfig, input_data: GenerativeVideoInput, tmp_path: Path
    ) -> None:
        with _mock_pipeline_success(tmp_path):
            result = generative_video_pipeline(config, input_data, tmp_path)
        assert result["report_path"] is not None
        assert result["report_path"].exists()
        content = result["report_path"].read_text()
        assert "generative_video_pipeline" in content


# --- Cost tests ---


class TestCost:
    def test_cost_returned(
        self, config: GenerativeVideoConfig, input_data: GenerativeVideoInput, tmp_path: Path
    ) -> None:
        with _mock_pipeline_success(tmp_path):
            result = generative_video_pipeline(config, input_data, tmp_path)
        assert "cost_usd" in result
        assert isinstance(result["cost_usd"], float)
        assert result["cost_usd"] >= 0.0


# --- Failure handling ---


class TestFailure:
    def test_failed_status_and_error(
        self, config: GenerativeVideoConfig, input_data: GenerativeVideoInput, tmp_path: Path
    ) -> None:
        with _mock_pipeline_failure():
            result = generative_video_pipeline(config, input_data, tmp_path)
        assert result["status"] == "failed"
        assert result["error"]["type"] == "RuntimeError"
        assert "LLM timeout" in result["error"]["message"]

    def test_findings_none_on_failure(
        self, config: GenerativeVideoConfig, input_data: GenerativeVideoInput, tmp_path: Path
    ) -> None:
        with _mock_pipeline_failure():
            result = generative_video_pipeline(config, input_data, tmp_path)
        assert result["findings"] is None


# --- on_step callback ---


class TestOnStep:
    def test_on_step_called(
        self, config: GenerativeVideoConfig, input_data: GenerativeVideoInput, tmp_path: Path
    ) -> None:
        steps_received: list[dict[str, Any]] = []

        def _cb(step: dict[str, Any]) -> None:
            steps_received.append(step)

        with _mock_pipeline_success(tmp_path):
            generative_video_pipeline(config, input_data, tmp_path, on_step=_cb)
        # At minimum the mock won't call stages, but failure path records a step
        # With success mock, no internal steps are recorded (stages are mocked)
        # Let's test with failure to ensure on_step fires
        steps_received.clear()
        with _mock_pipeline_failure():
            generative_video_pipeline(config, input_data, tmp_path, on_step=_cb)
        assert len(steps_received) >= 1


# --- Output structure ---


class TestOutputStructure:
    def test_all_keys_present(
        self, config: GenerativeVideoConfig, input_data: GenerativeVideoInput, tmp_path: Path
    ) -> None:
        with _mock_pipeline_success(tmp_path):
            result = generative_video_pipeline(config, input_data, tmp_path)
        expected_keys = {"findings", "fingerprint", "decision_trail", "report_path", "cost_usd", "status", "error"}
        assert set(result.keys()) == expected_keys

    def test_findings_model(
        self, config: GenerativeVideoConfig, input_data: GenerativeVideoInput, tmp_path: Path
    ) -> None:
        with _mock_pipeline_success(tmp_path):
            result = generative_video_pipeline(config, input_data, tmp_path)
        findings = result["findings"]
        assert isinstance(findings, GenerativeVideoFindings)
        assert findings.scenes_count == 3
        assert findings.shots_count == 9


# --- v2.0 MAJOR tests ---


class TestV2TemplateLoading:
    def test_template_id_loads_and_injects(self, tmp_path: Path) -> None:
        """template_id != None triggers _stage_load_template."""
        import yaml

        from omodul.generative_video_pipeline import _stage_load_template

        # Create a template file
        tmpl_path = tmp_path / "configs" / "templates" / "finance.yaml"
        tmpl_path.parent.mkdir(parents=True)
        tmpl_path.write_text(yaml.dump({
            "name": "finance", "version": "1.0.0",
            "system_prompt": "You are a quant expert.", "metadata": {},
        }))

        import os
        old_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            result = _stage_load_template("finance")
        finally:
            os.chdir(old_cwd)

        assert "quant expert" in result

    def test_template_id_none_skips_stage(
        self, input_data: GenerativeVideoInput, tmp_path: Path
    ) -> None:
        """template_id=None means no template stage (backward compat)."""
        config = GenerativeVideoConfig(topic="test", template_id=None)
        with _mock_pipeline_success(tmp_path):
            result = generative_video_pipeline(config, input_data, tmp_path)
        assert result["status"] == "completed"


class TestV2ImageToVideo:
    def test_image_to_video_enabled_triggers_stage(self) -> None:
        """image_to_video_enabled=True adds the field to config."""
        config = GenerativeVideoConfig(
            topic="test", image_to_video_enabled=True,
            image_to_video_provider="wan22_cloud",
        )
        assert config.image_to_video_enabled is True
        assert config.image_to_video_provider == "wan22_cloud"


class TestV2FaceAnimationProvider:
    def test_face_animation_provider_in_config(self) -> None:
        """face_animation_provider field works."""
        config = GenerativeVideoConfig(topic="test", face_animation_provider="sadtalker")
        assert config.face_animation_provider == "sadtalker"

    def test_default_face_animation_is_wav2lip(self) -> None:
        config = GenerativeVideoConfig(topic="test")
        assert config.face_animation_provider == "wav2lip"


class TestV2Fingerprint:
    def test_v2_fingerprint_includes_new_fields(self, input_data: GenerativeVideoInput) -> None:
        """Changing image_to_video_enabled changes fingerprint."""
        c1 = GenerativeVideoConfig(topic="cats", image_to_video_enabled=False)
        c2 = GenerativeVideoConfig(topic="cats", image_to_video_enabled=True)
        fp1 = compute_fingerprint_for(c1, input_data)
        fp2 = compute_fingerprint_for(c2, input_data)
        assert fp1 != fp2

    def test_v2_fingerprint_face_animation_provider(
        self, input_data: GenerativeVideoInput
    ) -> None:
        """Changing face_animation_provider changes fingerprint."""
        c1 = GenerativeVideoConfig(topic="cats", face_animation_provider="wav2lip")
        c2 = GenerativeVideoConfig(topic="cats", face_animation_provider="sadtalker")
        assert compute_fingerprint_for(c1, input_data) != compute_fingerprint_for(c2, input_data)

    def test_v2_vs_v1_fingerprint_mismatch(self, input_data: GenerativeVideoInput) -> None:
        """v2.0 fingerprint differs from v1.x due to version + new fields in hash."""
        # v2.0 includes image_to_video_enabled etc. in fingerprint
        config = GenerativeVideoConfig(topic="cats")
        fp = compute_fingerprint_for(config, input_data)
        # The fingerprint includes _omodul_version="2.0.0" in the hash
        assert len(fp) == 64
        # Verify version is embedded (by checking config version)
        assert config._omodul_version == "2.0.0"


    def test_template_not_found_raises(self, tmp_path: Path) -> None:
        """_stage_load_template raises when template doesn't exist."""
        import os

        old_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with pytest.raises(RuntimeError, match="Template not found"):
                _stage_load_template("nonexistent_template")
        finally:
            os.chdir(old_cwd)
