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
