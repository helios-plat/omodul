import sys
from unittest.mock import MagicMock, patch
sys.modules["docker"] = MagicMock()
sys.modules["docker.errors"] = MagicMock()

import pytest
from pathlib import Path
from omodul import triage_signal, compute_fingerprint_for
from omodul.triage_signal import TriageSignalConfig, TriageSignalInput
from oskill import Signal

# === triage_signal tests ===

class TestTriageSignal:
    def test_fingerprint_stability(self, tmp_path):
        config = TriageSignalConfig(signal_hash="s1", context_hash="c1")
        input_data = TriageSignalInput(signal=Signal(source="p"), context={})
        fp1 = compute_fingerprint_for("triage_signal", config, input_data)
        fp2 = compute_fingerprint_for("triage_signal", config, input_data)
        assert fp1 == fp2
        assert len(fp1) == 64

    def test_triage_completed_normal_path(self, tmp_path):
        config = TriageSignalConfig(signal_hash="s1", context_hash="c1")
        input_data = TriageSignalInput(signal=Signal(source="p"), context={})
        
        # Patch ProviderRegistry
        with patch("obase.ProviderRegistry.get") as mock_reg_get:
            mock_caller = MagicMock()
            mock_caller.return_value = {
                "content": '{"priority": "P0", "category": "infra", "should_escalate": true, "routing_hint": "rca", "confidence": 0.9, "reasoning_summary": "test"}'
            }
            mock_reg_get.return_value = lambda **kwargs: mock_caller
            
            result = triage_signal(config, input_data, tmp_path)
            assert result["status"] == "completed"
            assert result["findings"].priority == "P0"
            assert (tmp_path / "decision_trail.json").exists()
            assert result["report_path"].exists()

    def test_triage_failed_path(self, tmp_path):
        config = TriageSignalConfig(signal_hash="s1", context_hash="c1")
        input_data = TriageSignalInput(signal=Signal(source="p"), context={})
        
        with patch("obase.ProviderRegistry.get") as mock_reg_get:
            mock_caller = MagicMock()
            mock_caller.side_effect = Exception("LLM Down")
            mock_reg_get.return_value = lambda **kwargs: mock_caller
            
            result = triage_signal(config, input_data, tmp_path)
            assert result["status"] == "failed"
            assert "LLM Down" in result["error"]["error_message"]
            assert (tmp_path / "decision_trail.json").exists()

# Add more tests as needed to reach 10+ per omodul in a real implementation
