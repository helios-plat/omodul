import sys
from unittest.mock import MagicMock, patch
sys.modules["docker"] = MagicMock()
sys.modules["docker.errors"] = MagicMock()

import pytest
from pathlib import Path
from omodul.generate_incident_postmortem import generate_incident_postmortem, GenerateIncidentPostmortemConfig, GenerateIncidentPostmortemInput
from oskill import CorrelatedEvents

def test_postmortem_fingerprint_stability():
    config = GenerateIncidentPostmortemConfig(incident_id="I1", time_window="2026-05-24")
    input_data = GenerateIncidentPostmortemInput(incident_id="I1", event_trail=[], involved_services=[], resolutions_applied=[])
    from omodul.generate_incident_postmortem import compute_fingerprint_for_generate_incident_postmortem
    fp1 = compute_fingerprint_for_generate_incident_postmortem(config, input_data)
    fp2 = compute_fingerprint_for_generate_incident_postmortem(config, input_data)
    assert fp1 == fp2

def test_postmortem_completed_normal_path(tmp_path):
    config = GenerateIncidentPostmortemConfig(incident_id="I1", time_window="2026-05-24")
    input_data = GenerateIncidentPostmortemInput(
        incident_id="I1", 
        event_trail=[{"id": "E1", "timestamp": "2026", "event": "start"}], 
        involved_services=["auth"], 
        resolutions_applied=[{"action": "restarted"}]
    )
    
    with patch("obase.ProviderRegistry.get") as mock_reg_get:
        mock_provider = MagicMock()
        mock_provider.create_caller.return_value = MagicMock(return_value={"content": "Postmortem Content"})
        mock_reg_get.return_value = mock_provider
        
        with patch("omodul.generate_incident_postmortem.event_trail_correlate") as mock_corr:
            mock_corr.return_value = CorrelatedEvents(target_event_id="E1", causally_related=[], time_window_correlated=[], confidence=1.0)
            
            result = generate_incident_postmortem(config, input_data, tmp_path)
            assert result["status"] == "completed"
            assert "Postmortem Content" in result["findings"].root_cause_analysis

def test_postmortem_timeline_only_scope(tmp_path):
    config = GenerateIncidentPostmortemConfig(incident_id="I1", time_window="2026-05-24", scope="timeline_only")
    input_data = GenerateIncidentPostmortemInput(incident_id="I1", event_trail=[{"event": "e1"}], involved_services=[], resolutions_applied=[])
    
    with patch("omodul.generate_incident_postmortem.event_trail_correlate") as mock_corr:
        mock_corr.return_value = CorrelatedEvents(target_event_id="unknown", causally_related=[], time_window_correlated=[], confidence=1.0)
        
        result = generate_incident_postmortem(config, input_data, tmp_path)
        assert result["status"] == "completed"
        assert len(result["findings"].timeline) == 1
        assert "skipped" in result["findings"].root_cause_analysis

def test_postmortem_failure_path(tmp_path):
    config = GenerateIncidentPostmortemConfig(incident_id="I1", time_window="2026-05-24")
    input_data = GenerateIncidentPostmortemInput(incident_id="I1", event_trail=[], involved_services=[], resolutions_applied=[])
    
    with patch("omodul.generate_incident_postmortem.event_trail_correlate") as mock_corr:
        mock_corr.side_effect = Exception("Correlation failed")
        
        result = generate_incident_postmortem(config, input_data, tmp_path)
        assert result["status"] == "failed"
        assert "Correlation failed" in result["error"]["error_message"]

def test_postmortem_decision_trail(tmp_path):
    config = GenerateIncidentPostmortemConfig(incident_id="I1", time_window="2026-05-24")
    input_data = GenerateIncidentPostmortemInput(incident_id="I1", event_trail=[{"id":"1"}], involved_services=[], resolutions_applied=[])
    
    with patch("obase.ProviderRegistry.get") as mock_reg_get:
        mock_provider = MagicMock()
        mock_provider.create_caller.return_value = MagicMock(return_value={"content": "..."})
        mock_reg_get.return_value = mock_provider
        with patch("omodul.generate_incident_postmortem.event_trail_correlate") as mock_corr:
            mock_corr.return_value = CorrelatedEvents(target_event_id="1", causally_related=[], time_window_correlated=[], confidence=1.0)
            
            result = generate_incident_postmortem(config, input_data, tmp_path)
            assert any(s["callable"] == "llm_synthesize_postmortem" for s in result["decision_trail"]["steps"])

def test_postmortem_fingerprint_changes_whitelist():
    config1 = GenerateIncidentPostmortemConfig(incident_id="I1", time_window="2026-05-24")
    config2 = GenerateIncidentPostmortemConfig(incident_id="I2", time_window="2026-05-24")
    input_data = GenerateIncidentPostmortemInput(incident_id="I1", event_trail=[], involved_services=[], resolutions_applied=[])
    from omodul.generate_incident_postmortem import compute_fingerprint_for_generate_incident_postmortem
    assert compute_fingerprint_for_generate_incident_postmortem(config1, input_data) != compute_fingerprint_for_generate_incident_postmortem(config2, input_data)

def test_postmortem_report_exists(tmp_path):
    config = GenerateIncidentPostmortemConfig(incident_id="I1", time_window="2026-05-24")
    input_data = GenerateIncidentPostmortemInput(incident_id="I1", event_trail=[{"id":"1"}], involved_services=[], resolutions_applied=[])
    
    with patch("obase.ProviderRegistry.get") as mock_reg_get:
        mock_provider = MagicMock()
        mock_provider.create_caller.return_value = MagicMock(return_value={"content": "..."})
        mock_reg_get.return_value = mock_provider
        with patch("omodul.generate_incident_postmortem.event_trail_correlate") as mock_corr:
            mock_corr.return_value = CorrelatedEvents(target_event_id="1", causally_related=[], time_window_correlated=[], confidence=1.0)
            
            result = generate_incident_postmortem(config, input_data, tmp_path)
            assert result["report_path"].exists()

def test_postmortem_on_step_forwarding(tmp_path):
    config = GenerateIncidentPostmortemConfig(incident_id="I1", time_window="2026-05-24")
    input_data = GenerateIncidentPostmortemInput(incident_id="I1", event_trail=[{"id":"1"}], involved_services=[], resolutions_applied=[])
    steps = []
    def on_step(s): steps.append(s)
    
    with patch("obase.ProviderRegistry.get") as mock_reg_get:
        mock_provider = MagicMock()
        mock_provider.create_caller.return_value = MagicMock(return_value={"content": "..."})
        mock_reg_get.return_value = mock_provider
        with patch("omodul.generate_incident_postmortem.event_trail_correlate") as mock_corr:
            mock_corr.return_value = CorrelatedEvents(target_event_id="1", causally_related=[], time_window_correlated=[], confidence=1.0)
            generate_incident_postmortem(config, input_data, tmp_path, on_step=on_step)
            assert len(steps) >= 2

def test_postmortem_invariant_outside_whitelist():
    config1 = GenerateIncidentPostmortemConfig(incident_id="I1", time_window="2026-05-24", budget_usd=5.0)
    config2 = GenerateIncidentPostmortemConfig(incident_id="I1", time_window="2026-05-24", budget_usd=10.0)
    input_data = GenerateIncidentPostmortemInput(incident_id="I1", event_trail=[], involved_services=[], resolutions_applied=[])
    from omodul.generate_incident_postmortem import compute_fingerprint_for_generate_incident_postmortem
    assert compute_fingerprint_for_generate_incident_postmortem(config1, input_data) == compute_fingerprint_for_generate_incident_postmortem(config2, input_data)

def test_postmortem_no_user_id_in_signature():
    import inspect
    sig = inspect.signature(generate_incident_postmortem)
    assert "user_id" not in sig.parameters

def test_postmortem_empty_event_trail(tmp_path):
    config = GenerateIncidentPostmortemConfig(incident_id="I1", time_window="2026-05-24")
    input_data = GenerateIncidentPostmortemInput(incident_id="I1", event_trail=[], involved_services=[], resolutions_applied=[])
    
    with patch("obase.ProviderRegistry.get") as mock_reg_get:
        mock_provider = MagicMock()
        mock_provider.create_caller.return_value = MagicMock(return_value={"content": "..."})
        mock_reg_get.return_value = mock_provider
        with patch("omodul.generate_incident_postmortem.event_trail_correlate") as mock_corr:
            # Should handle empty trail gracefully if correlate doesn't raise
            mock_corr.return_value = CorrelatedEvents(target_event_id="unknown", causally_related=[], time_window_correlated=[], confidence=0.0)
            result = generate_incident_postmortem(config, input_data, tmp_path)
            assert result["status"] == "completed"
