import sys
from unittest.mock import MagicMock, patch
sys.modules["docker"] = MagicMock()
sys.modules["docker.errors"] = MagicMock()

import pytest
from pathlib import Path
from omodul.triage_signal import triage_signal, TriageSignalConfig, TriageSignalInput, TriageSignalFindings
from oskill import Signal

def test_triage_signal_fingerprint_stability():
    config = TriageSignalConfig(signal_hash="s1", context_hash="c1")
    input_data = TriageSignalInput(signal=Signal(source="p"), context={})
    from omodul.triage_signal import compute_fingerprint_for_triage_signal
    fp1 = compute_fingerprint_for_triage_signal(config, input_data)
    fp2 = compute_fingerprint_for_triage_signal(config, input_data)
    assert fp1 == fp2
    assert len(fp1) == 64

def test_triage_signal_fingerprint_changes():
    config1 = TriageSignalConfig(signal_hash="s1", context_hash="c1")
    config2 = TriageSignalConfig(signal_hash="s2", context_hash="c1")
    input_data = TriageSignalInput(signal=Signal(source="p"), context={})
    from omodul.triage_signal import compute_fingerprint_for_triage_signal
    fp1 = compute_fingerprint_for_triage_signal(config1, input_data)
    fp2 = compute_fingerprint_for_triage_signal(config2, input_data)
    assert fp1 != fp2

def test_triage_signal_completed_normal_path(tmp_path):
    config = TriageSignalConfig(signal_hash="s1", context_hash="c1")
    input_data = TriageSignalInput(signal=Signal(source="p"), context={})
    
    with patch("obase.ProviderRegistry.get") as mock_reg_get:
        mock_provider = MagicMock()
        mock_caller = MagicMock()
        mock_caller.return_value = {
            "content": '{"priority": "P0", "category": "infra", "should_escalate": true, "routing_hint": "rca", "confidence": 0.9, "reasoning_summary": "test"}'
        }
        mock_provider.create_caller.return_value = mock_caller
        mock_reg_get.return_value = mock_provider
        
        result = triage_signal(config, input_data, tmp_path)
        assert result["status"] == "completed"
        assert result["findings"].priority == "P0"
        assert (tmp_path / "decision_trail.json").exists()
        assert result["report_path"].exists()

def test_triage_signal_llm_failure(tmp_path):
    config = TriageSignalConfig(signal_hash="s1", context_hash="c1")
    input_data = TriageSignalInput(signal=Signal(source="p"), context={})
    
    with patch("obase.ProviderRegistry.get") as mock_reg_get:
        mock_provider = MagicMock()
        mock_provider.create_caller.side_effect = Exception("LLM Timeout")
        mock_reg_get.return_value = mock_provider
        
        result = triage_signal(config, input_data, tmp_path)
        assert result["status"] == "failed"
        assert "LLM Timeout" in result["error"]["error_message"]

def test_triage_signal_malformed_json(tmp_path):
    config = TriageSignalConfig(signal_hash="s1", context_hash="c1")
    input_data = TriageSignalInput(signal=Signal(source="p"), context={})
    
    with patch("obase.ProviderRegistry.get") as mock_reg_get:
        mock_provider = MagicMock()
        mock_caller = MagicMock(return_value={"content": "not json"})
        mock_provider.create_caller.return_value = mock_caller
        mock_reg_get.return_value = mock_provider
        
        result = triage_signal(config, input_data, tmp_path)
        assert result["status"] == "failed"

def test_triage_signal_decision_trail_structure(tmp_path):
    config = TriageSignalConfig(signal_hash="s1", context_hash="c1")
    input_data = TriageSignalInput(signal=Signal(source="p"), context={})
    
    with patch("obase.ProviderRegistry.get") as mock_reg_get:
        mock_provider = MagicMock()
        mock_caller = MagicMock(return_value={"content": '{"priority": "P2", "category": "user", "should_escalate": false, "routing_hint": "none", "confidence": 0.5, "reasoning_summary": "low"}'})
        mock_provider.create_caller.return_value = mock_caller
        mock_reg_get.return_value = mock_provider
        
        result = triage_signal(config, input_data, tmp_path)
        trail = result["decision_trail"]
        assert trail["omodul_name"] == "triage_signal"
        assert len(trail["steps"]) == 1
        assert trail["steps"][0]["callable"] == "llm_triage"

def test_triage_signal_report_content(tmp_path):
    config = TriageSignalConfig(signal_hash="s1", context_hash="c1")
    input_data = TriageSignalInput(signal=Signal(source="p"), context={})
    
    with patch("obase.ProviderRegistry.get") as mock_reg_get:
        mock_provider = MagicMock()
        mock_caller = MagicMock(return_value={"content": '{"priority": "P1", "category": "db", "should_escalate": true, "routing_hint": "db_team", "confidence": 0.8, "reasoning_summary": "slow queries"}'})
        mock_provider.create_caller.return_value = mock_caller
        mock_reg_get.return_value = mock_provider
        
        result = triage_signal(config, input_data, tmp_path)
        report = result["report_path"].read_text()
        assert "## 1. Executive Summary" in report
        assert "## 2. Configuration" in report
        assert "## 3. Findings" in report
        assert "P1" in report

def test_triage_signal_on_step_callback(tmp_path):
    config = TriageSignalConfig(signal_hash="s1", context_hash="c1")
    input_data = TriageSignalInput(signal=Signal(source="p"), context={})
    
    steps = []
    def on_step(step):
        steps.append(step)
        
    with patch("obase.ProviderRegistry.get") as mock_reg_get:
        mock_provider = MagicMock()
        mock_caller = MagicMock(return_value={"content": '{"priority": "P3", "category": "info", "should_escalate": false, "routing_hint": "log", "confidence": 1.0, "reasoning_summary": "ok"}'})
        mock_provider.create_caller.return_value = mock_caller
        mock_reg_get.return_value = mock_provider
        
        triage_signal(config, input_data, tmp_path, on_step=on_step)
        assert len(steps) == 1

def test_triage_signal_invariant_outside_whitelist():
    config1 = TriageSignalConfig(signal_hash="s1", context_hash="c1", llm_provider="anthropic")
    config2 = TriageSignalConfig(signal_hash="s1", context_hash="c1", llm_provider="openai")
    input_data = TriageSignalInput(signal=Signal(source="p"), context={})
    from omodul.triage_signal import compute_fingerprint_for_triage_signal
    fp1 = compute_fingerprint_for_triage_signal(config1, input_data)
    fp2 = compute_fingerprint_for_triage_signal(config2, input_data)
    assert fp1 == fp2

def test_triage_signal_no_user_id_in_signature():
    import inspect
    sig = inspect.signature(triage_signal)
    assert "user_id" not in sig.parameters

def test_triage_signal_json_with_extra_text(tmp_path):
    """Test LLM returning JSON wrapped in text."""
    config = TriageSignalConfig(signal_hash="s1", context_hash="c1")
    input_data = TriageSignalInput(signal=Signal(source="p"), context={})
    
    with patch("obase.ProviderRegistry.get") as mock_reg_get:
        mock_provider = MagicMock()
        mock_caller = MagicMock()
        mock_caller.return_value = {
            "content": 'Here is the analysis: {"priority": "P0", "category": "infra", "should_escalate": true, "routing_hint": "rca", "confidence": 0.9, "reasoning_summary": "test"} Hope this helps!'
        }
        mock_provider.create_caller.return_value = mock_caller
        mock_reg_get.return_value = mock_provider
        
        result = triage_signal(config, input_data, tmp_path)
        assert result["status"] == "completed"
        assert result["findings"].priority == "P0"
