import sys
from unittest.mock import MagicMock, patch
sys.modules["docker"] = MagicMock()
sys.modules["docker.errors"] = MagicMock()

import pytest
from pathlib import Path
from omodul.diagnose_root_cause import diagnose_root_cause, DiagnoseRootCauseConfig, DiagnoseRootCauseInput
from oskill import Signal, InvestigationOutcome

def test_diagnose_root_cause_fingerprint_stability():
    config = DiagnoseRootCauseConfig(signal_hash="s1", available_tools_hash="t1")
    input_data = DiagnoseRootCauseInput(signal=Signal(source="p"), available_tool_names=[])
    from omodul.diagnose_root_cause import compute_fingerprint_for_diagnose_root_cause
    fp1 = compute_fingerprint_for_diagnose_root_cause(config, input_data)
    fp2 = compute_fingerprint_for_diagnose_root_cause(config, input_data)
    assert fp1 == fp2

def test_diagnose_root_cause_completed_normal_path(tmp_path):
    config = DiagnoseRootCauseConfig(signal_hash="s1", available_tools_hash="t1")
    input_data = DiagnoseRootCauseInput(signal=Signal(source="p"), available_tool_names=[])
    
    with patch("obase.ProviderRegistry.get") as mock_reg_get:
        mock_provider = MagicMock()
        mock_reg_get.return_value = mock_provider
        
        with patch("omodul.diagnose_root_cause.agentic_investigate_loop") as mock_loop:
            mock_loop.return_value = InvestigationOutcome(
                steps_taken=2,
                steps=[],
                final_conclusion={"root_cause_hypothesis": "Test Hypothesis", "confidence": 0.9, "suggested_actions": ["fix it"]},
                stopped_reason="confidence_threshold"
            )
            
            result = diagnose_root_cause(config, input_data, tmp_path)
            assert result["status"] == "completed"
            assert result["findings"].root_cause_hypothesis == "Test Hypothesis"
            assert result["findings"].requires_human == False

def test_diagnose_root_cause_requires_human_max_steps(tmp_path):
    config = DiagnoseRootCauseConfig(signal_hash="s1", available_tools_hash="t1")
    input_data = DiagnoseRootCauseInput(signal=Signal(source="p"), available_tool_names=[])
    
    with patch("obase.ProviderRegistry.get") as mock_reg_get:
        mock_reg_get.return_value = MagicMock()
        with patch("omodul.diagnose_root_cause.agentic_investigate_loop") as mock_loop:
            mock_loop.return_value = InvestigationOutcome(
                steps_taken=20,
                steps=[],
                final_conclusion={"root_cause_hypothesis": "Inconclusive", "confidence": 0.5},
                stopped_reason="max_steps"
            )
            
            result = diagnose_root_cause(config, input_data, tmp_path)
            assert result["findings"].requires_human == True
            assert "max_steps" in result["findings"].requires_human_reason

def test_diagnose_root_cause_failure_path(tmp_path):
    config = DiagnoseRootCauseConfig(signal_hash="s1", available_tools_hash="t1")
    input_data = DiagnoseRootCauseInput(signal=Signal(source="p"), available_tool_names=[])
    
    with patch("obase.ProviderRegistry.get") as mock_reg_get:
        mock_reg_get.side_effect = Exception("Registry Error")
        
        result = diagnose_root_cause(config, input_data, tmp_path)
        assert result["status"] == "failed"
        assert (tmp_path / "decision_trail.json").exists()

def test_diagnose_root_cause_decision_trail(tmp_path):
    config = DiagnoseRootCauseConfig(signal_hash="s1", available_tools_hash="t1")
    input_data = DiagnoseRootCauseInput(signal=Signal(source="p"), available_tool_names=["t1"])
    
    with patch("obase.ProviderRegistry.get") as mock_reg_get:
        mock_reg_get.return_value = MagicMock()
        with patch("omodul.diagnose_root_cause.agentic_investigate_loop") as mock_loop:
            mock_loop.return_value = InvestigationOutcome(
                steps_taken=1,
                steps=[],
                final_conclusion={"confidence": 0.9},
                stopped_reason="confidence_threshold"
            )
            
            result = diagnose_root_cause(config, input_data, tmp_path)
            trail = result["decision_trail"]
            assert trail["omodul_name"] == "diagnose_root_cause"
            assert any(s["callable"] == "agentic_investigate_loop" for s in trail["steps"])

def test_diagnose_root_cause_fingerprint_changes_whitelist():
    config1 = DiagnoseRootCauseConfig(signal_hash="s1", available_tools_hash="t1", max_steps=10)
    config2 = DiagnoseRootCauseConfig(signal_hash="s1", available_tools_hash="t1", max_steps=20)
    input_data = DiagnoseRootCauseInput(signal=Signal(source="p"), available_tool_names=[])
    from omodul.diagnose_root_cause import compute_fingerprint_for_diagnose_root_cause
    assert compute_fingerprint_for_diagnose_root_cause(config1, input_data) != compute_fingerprint_for_diagnose_root_cause(config2, input_data)

def test_diagnose_root_cause_report(tmp_path):
    config = DiagnoseRootCauseConfig(signal_hash="s1", available_tools_hash="t1")
    input_data = DiagnoseRootCauseInput(signal=Signal(source="p"), available_tool_names=[])
    
    with patch("obase.ProviderRegistry.get") as mock_reg_get:
        mock_reg_get.return_value = MagicMock()
        with patch("omodul.diagnose_root_cause.agentic_investigate_loop") as mock_loop:
            mock_loop.return_value = InvestigationOutcome(
                steps_taken=1,
                steps=[],
                final_conclusion={"root_cause_hypothesis": "Bug", "confidence": 1.0},
                stopped_reason="confidence_threshold"
            )
            
            result = diagnose_root_cause(config, input_data, tmp_path)
            assert result["report_path"].exists()
            assert "Bug" in result["report_path"].read_text()

def test_diagnose_root_cause_on_step_forwarding(tmp_path):
    config = DiagnoseRootCauseConfig(signal_hash="s1", available_tools_hash="t1")
    input_data = DiagnoseRootCauseInput(signal=Signal(source="p"), available_tool_names=[])
    
    steps = []
    def on_step(s):
        steps.append(s)
        
    with patch("obase.ProviderRegistry.get") as mock_reg_get:
        mock_reg_get.return_value = MagicMock()
        with patch("omodul.diagnose_root_cause.agentic_investigate_loop") as mock_loop:
            mock_loop.return_value = InvestigationOutcome(
                steps_taken=1, steps=[], final_conclusion={}, stopped_reason="max_steps"
            )
            diagnose_root_cause(config, input_data, tmp_path, on_step=on_step)
            assert len(steps) > 0

def test_diagnose_root_cause_invariant_outside_whitelist():
    config1 = DiagnoseRootCauseConfig(signal_hash="s1", available_tools_hash="t1", llm_provider="anthropic")
    config2 = DiagnoseRootCauseConfig(signal_hash="s1", available_tools_hash="t1", llm_provider="openai")
    input_data = DiagnoseRootCauseInput(signal=Signal(source="p"), available_tool_names=[])
    from omodul.diagnose_root_cause import compute_fingerprint_for_diagnose_root_cause
    assert compute_fingerprint_for_diagnose_root_cause(config1, input_data) == compute_fingerprint_for_diagnose_root_cause(config2, input_data)

def test_diagnose_root_cause_no_user_id_in_signature():
    import inspect
    sig = inspect.signature(diagnose_root_cause)
    assert "user_id" not in sig.parameters

def test_diagnose_root_cause_partial_findings_on_failure(tmp_path):
    config = DiagnoseRootCauseConfig(signal_hash="s1", available_tools_hash="t1")
    input_data = DiagnoseRootCauseInput(signal=Signal(source="p"), available_tool_names=[])
    
    with patch("obase.ProviderRegistry.get") as mock_reg_get:
        mock_reg_get.return_value = MagicMock()
        with patch("omodul.diagnose_root_cause._stage_investigation") as mock_stage:
            mock_stage.side_effect = Exception("Finalization error")
            result = diagnose_root_cause(config, input_data, tmp_path)
            assert result["status"] == "failed"
            assert result["findings"] is None
