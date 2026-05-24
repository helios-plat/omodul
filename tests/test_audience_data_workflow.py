"""Tests for omodul.audience_data_workflow — 4 pillars + 6 stages."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from omodul.audience_data_workflow import (
    AudienceDataConfig,
    AudienceDataFindings,
    AudienceDataInput,
    audience_data_workflow,
    compute_fingerprint_for,
)


@pytest.fixture()
def yt_config() -> AudienceDataConfig:
    return AudienceDataConfig(platform="youtube", video_ids=["vid1", "vid2"])


@pytest.fixture()
def bili_config() -> AudienceDataConfig:
    return AudienceDataConfig(platform="bilibili", video_ids=["BV1xx"])


@pytest.fixture()
def yt_input() -> AudienceDataInput:
    return AudienceDataInput(oauth_token="test_token")


@pytest.fixture()
def bili_input() -> AudienceDataInput:
    return AudienceDataInput(cookies={"SESSDATA": "x"})


def _mock_stages_success(tmp_path: Path):
    async def _fake(*args: Any, **kw: Any) -> AudienceDataFindings:
        return AudienceDataFindings(
            videos_analyzed=2, total_views=1000, total_comments=50,
            learnings=["Use shorter intros"],
        )
    return patch("omodul.audience_data_workflow._run_stages", side_effect=_fake)


def _mock_stages_failure():
    async def _fail(*args: Any, **kw: Any) -> None:
        raise RuntimeError("YouTube API quota exceeded")
    return patch("omodul.audience_data_workflow._run_stages", side_effect=_fail)


# --- Fingerprint tests ---


class TestFingerprint:
    def test_change_video_ids_changes_fp(self, yt_input: AudienceDataInput) -> None:
        c1 = AudienceDataConfig(platform="youtube", video_ids=["a"])
        c2 = AudienceDataConfig(platform="youtube", video_ids=["b"])
        assert compute_fingerprint_for(c1, yt_input) != compute_fingerprint_for(c2, yt_input)

    def test_change_platform_changes_fp(self, yt_input: AudienceDataInput) -> None:
        c1 = AudienceDataConfig(platform="youtube", video_ids=["a"])
        c2 = AudienceDataConfig(platform="bilibili", video_ids=["a"])
        assert compute_fingerprint_for(c1, yt_input) != compute_fingerprint_for(c2, yt_input)

    def test_change_analysis_depth_changes_fp(self, yt_input: AudienceDataInput) -> None:
        c1 = AudienceDataConfig(platform="youtube", video_ids=["a"], analysis_depth="basic")
        c2 = AudienceDataConfig(platform="youtube", video_ids=["a"], analysis_depth="deep")
        assert compute_fingerprint_for(c1, yt_input) != compute_fingerprint_for(c2, yt_input)

    def test_non_fingerprint_field_no_change(self, yt_input: AudienceDataInput) -> None:
        c1 = AudienceDataConfig(platform="youtube", video_ids=["a"], max_comments_per_video=50)
        c2 = AudienceDataConfig(platform="youtube", video_ids=["a"], max_comments_per_video=200)
        assert compute_fingerprint_for(c1, yt_input) == compute_fingerprint_for(c2, yt_input)

    def test_fingerprint_deterministic(
        self, yt_config: AudienceDataConfig, yt_input: AudienceDataInput
    ) -> None:
        fp1 = compute_fingerprint_for(yt_config, yt_input)
        fp2 = compute_fingerprint_for(yt_config, yt_input)
        assert fp1 == fp2 and len(fp1) == 64


# --- Pipeline success ---


class TestPipelineSuccess:
    def test_completed_status(
        self, yt_config: AudienceDataConfig, yt_input: AudienceDataInput, tmp_path: Path
    ) -> None:
        with _mock_stages_success(tmp_path):
            result = audience_data_workflow(yt_config, yt_input, tmp_path)
        assert result["status"] == "completed"
        assert result["error"] is None

    def test_findings_populated(
        self, yt_config: AudienceDataConfig, yt_input: AudienceDataInput, tmp_path: Path
    ) -> None:
        with _mock_stages_success(tmp_path):
            result = audience_data_workflow(yt_config, yt_input, tmp_path)
        findings = result["findings"]
        assert isinstance(findings, AudienceDataFindings)
        assert findings.videos_analyzed == 2
        assert findings.total_views == 1000

    def test_decision_trail_written(
        self, yt_config: AudienceDataConfig, yt_input: AudienceDataInput, tmp_path: Path
    ) -> None:
        with _mock_stages_success(tmp_path):
            result = audience_data_workflow(yt_config, yt_input, tmp_path)
        trail_file = tmp_path / "decision_trail.json"
        assert trail_file.exists()
        trail = json.loads(trail_file.read_text())
        assert trail["status"] == "completed"
        assert "fingerprint" in trail

    def test_report_generated(
        self, yt_config: AudienceDataConfig, yt_input: AudienceDataInput, tmp_path: Path
    ) -> None:
        with _mock_stages_success(tmp_path):
            result = audience_data_workflow(yt_config, yt_input, tmp_path)
        assert result["report_path"] is not None
        assert result["report_path"].exists()

    def test_cost_returned(
        self, yt_config: AudienceDataConfig, yt_input: AudienceDataInput, tmp_path: Path
    ) -> None:
        with _mock_stages_success(tmp_path):
            result = audience_data_workflow(yt_config, yt_input, tmp_path)
        assert isinstance(result["cost_usd"], float)
        assert result["cost_usd"] >= 0.0


# --- Pipeline failure ---


class TestPipelineFailure:
    def test_failed_status(
        self, yt_config: AudienceDataConfig, yt_input: AudienceDataInput, tmp_path: Path
    ) -> None:
        with _mock_stages_failure():
            result = audience_data_workflow(yt_config, yt_input, tmp_path)
        assert result["status"] == "failed"
        assert "quota" in result["error"]["message"]

    def test_trail_still_written_on_failure(
        self, yt_config: AudienceDataConfig, yt_input: AudienceDataInput, tmp_path: Path
    ) -> None:
        with _mock_stages_failure():
            audience_data_workflow(yt_config, yt_input, tmp_path)
        trail_file = tmp_path / "decision_trail.json"
        assert trail_file.exists()
        trail = json.loads(trail_file.read_text())
        assert trail["status"] == "failed"

    def test_findings_none_on_failure(
        self, yt_config: AudienceDataConfig, yt_input: AudienceDataInput, tmp_path: Path
    ) -> None:
        with _mock_stages_failure():
            result = audience_data_workflow(yt_config, yt_input, tmp_path)
        assert result["findings"] is None


# --- Output structure ---


class TestOutputStructure:
    def test_all_keys_present(
        self, yt_config: AudienceDataConfig, yt_input: AudienceDataInput, tmp_path: Path
    ) -> None:
        with _mock_stages_success(tmp_path):
            result = audience_data_workflow(yt_config, yt_input, tmp_path)
        expected = {"findings", "fingerprint", "decision_trail", "report_path", "cost_usd", "status", "error"}
        assert set(result.keys()) == expected

    def test_on_step_callback(
        self, yt_config: AudienceDataConfig, yt_input: AudienceDataInput, tmp_path: Path
    ) -> None:
        steps: list[dict[str, Any]] = []
        with _mock_stages_failure():
            audience_data_workflow(yt_config, yt_input, tmp_path, on_step=steps.append)
        assert len(steps) >= 1
