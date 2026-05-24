import json
import inspect
from pathlib import Path
from datetime import datetime, UTC, timedelta
from unittest.mock import MagicMock, patch, PropertyMock

import pytest
from pydantic import BaseModel

from obase.cost_tracker import CostTracker
from omodul._base_config import BaseConfig
from omodul.weekly_review_workflow import (
    weekly_review_workflow,
    compute_fingerprint_for,
    WeeklyReviewConfig,
    WeeklyReviewInput,
    ActivityItem,
)

@pytest.fixture
def sample_input():
    activities = [
        ActivityItem(activity_id="a1", activity_type="sub", title="t1", timestamp_utc="2024-05-20T10:00:00Z")
    ]
    return WeeklyReviewInput(
        activities=activities,
        window_start_utc=datetime.now(UTC) - timedelta(days=7),
        window_end_utc=datetime.now(UTC)
    )

def test_baseconfig_inheritance():
    """WeeklyReviewConfig.__mro__ 含 BaseConfig"""
    assert issubclass(WeeklyReviewConfig, BaseConfig)

def test_fingerprint_fields_classvar():
    """WeeklyReviewConfig._fingerprint_fields 是 ClassVar set"""
    fields = WeeklyReviewConfig._fingerprint_fields
    assert isinstance(fields, set)
    assert "time_window_days" in fields
    assert "title_prefix" in fields

def test_no_user_id_in_signature():
    """静态测试 inspect.signature, 无 user_id 参数"""
    sig = inspect.signature(weekly_review_workflow)
    assert "user_id" not in sig.parameters

def test_compute_fingerprint_for_exposed(sample_input):
    """from omodul import compute_fingerprint_for 可用"""
    config = WeeklyReviewConfig()
    fp = compute_fingerprint_for(config, sample_input)
    assert len(fp) == 64

def test_fingerprint_deterministic(sample_input):
    """同 config + 同 input → 同 fp (跑 2 次)"""
    c1 = WeeklyReviewConfig()
    c2 = WeeklyReviewConfig()
    fp1 = compute_fingerprint_for(c1, sample_input)
    fp2 = compute_fingerprint_for(c2, sample_input)
    assert fp1 == fp2

def test_fingerprint_changes_on_config_field(sample_input):
    """改 title_prefix → fp 变"""
    c1 = WeeklyReviewConfig(title_prefix="A")
    c2 = WeeklyReviewConfig(title_prefix="B")
    assert compute_fingerprint_for(c1, sample_input) != compute_fingerprint_for(c2, sample_input)

def test_fingerprint_unchanged_on_non_field(sample_input):
    """改 llm_provider → fp 不变"""
    c1 = WeeklyReviewConfig(llm_provider="p1")
    c2 = WeeklyReviewConfig(llm_provider="p2")
    assert compute_fingerprint_for(c1, sample_input) == compute_fingerprint_for(c2, sample_input)

@patch("obase.provider_registry.ProviderRegistry.get_caller")
def test_normal_path(mock_get_caller, sample_input, tmp_path):
    """status=completed + report 存在 + decision_trail.json 存在"""
    mock_llm = MagicMock(return_value={"content": "Summary text", "usage": {"cost_usd": 0.1}})
    mock_get_caller.return_value = mock_llm
    
    config = WeeklyReviewConfig()
    out = tmp_path / "out"
    res = weekly_review_workflow(config, sample_input, out)
    
    assert res["status"] == "completed"
    assert (out / "report.md").exists()
    assert (out / "decision_trail.json").exists()
    assert "Summary text" in res["findings"]["summary"]

@patch("obase.provider_registry.ProviderRegistry.get_caller")
def test_decision_trail_complete(mock_get_caller, sample_input, tmp_path):
    """trail 含 metadata + steps + cost_breakdown + status"""
    mock_llm = MagicMock(return_value={"content": "sum"})
    mock_get_caller.return_value = mock_llm
    
    res = weekly_review_workflow(WeeklyReviewConfig(), sample_input, tmp_path / "out")
    trail = res["decision_trail"]
    assert "status" in trail
    assert "steps" in trail
    assert "cost_breakdown" in trail
    assert trail["status"] == "completed"
    assert len(trail["steps"]) > 0

@patch("obase.provider_registry.ProviderRegistry.get_caller")
def test_report_seven_sections(mock_get_caller, sample_input, tmp_path):
    """报告含 7 个 ## section"""
    mock_llm = MagicMock(return_value={"content": "sum"})
    mock_get_caller.return_value = mock_llm
    
    out = tmp_path / "out"
    weekly_review_workflow(WeeklyReviewConfig(), sample_input, out)
    report = (out / "report.md").read_text()
    
    sections = [
        "## Summary", "## Config", "## Findings",
        "## Trail", "## Cost", "## Reproducibility"
    ]
    for s in sections:
        assert s in report

@patch("obase.provider_registry.ProviderRegistry.get_caller")
def test_failure_path_writes_trail(mock_get_caller, sample_input, tmp_path):
    """mock _stage_X raise → status=failed + trail 仍写 + .partial.md + .failed_marker"""
    mock_llm = MagicMock(side_effect=RuntimeError("LLM failed"))
    mock_get_caller.return_value = mock_llm
    
    out = tmp_path / "out"
    res = weekly_review_workflow(WeeklyReviewConfig(), sample_input, out)
    
    assert res["status"] == "failed"
    assert "RuntimeError" in res["error"]["type"]
    assert (out / "decision_trail.json").exists()
    assert (out / "report.partial.md").exists()
    assert (out / ".failed_marker").exists()

@patch("obase.provider_registry.ProviderRegistry.get_caller")
def test_no_raise_on_failure(mock_get_caller, sample_input, tmp_path):
    """mock raise → 主函数不抛, 返回 dict status=failed"""
    mock_get_caller.side_effect = ValueError("Registry error")
    res = weekly_review_workflow(WeeklyReviewConfig(), sample_input, tmp_path / "out")
    assert res["status"] == "failed"
    assert res["error"]["type"] == "ValueError"

@patch("obase.provider_registry.ProviderRegistry.get_caller")
@patch("omodul.weekly_review_workflow.CostTracker.total_usd", new_callable=PropertyMock)
def test_cost_tracker_used(mock_total_usd, mock_get_caller, sample_input, tmp_path):
    """mock LLM 后 cost_usd > 0"""
    mock_get_caller.return_value = MagicMock(return_value={"content": "sum"})
    mock_total_usd.return_value = 0.05
    res = weekly_review_workflow(WeeklyReviewConfig(), sample_input, tmp_path / "out")
    assert res["cost_usd"] > 0

@patch("obase.provider_registry.ProviderRegistry.get_caller")
def test_on_step_callback(mock_get_caller, sample_input, tmp_path):
    """注入 on_step, 期待被调用 N 次"""
    mock_get_caller.return_value = MagicMock(return_value={"content": "sum"})
    
    steps = []
    def callback(step):
        steps.append(step)
        
    weekly_review_workflow(WeeklyReviewConfig(), sample_input, tmp_path / "out", on_step=callback)
    assert len(steps) > 0

def test_empty_activities(tmp_path):
    """input.activities=[] → status=completed + total=0"""
    empty_input = WeeklyReviewInput(
        activities=[],
        window_start_utc=datetime.now(UTC) - timedelta(days=7),
        window_end_utc=datetime.now(UTC)
    )
    out = tmp_path / "out"
    res = weekly_review_workflow(WeeklyReviewConfig(), empty_input, out)
    assert res["status"] == "completed"
    report = (out / "report.md").read_text()
    assert "No activities found" in report
