import sys
from unittest.mock import MagicMock, patch
sys.modules["docker"] = MagicMock()
sys.modules["docker.errors"] = MagicMock()

import pytest
from pathlib import Path
from omodul.propose_action_plan import propose_action_plan, ProposeActionPlanConfig, ProposeActionPlanInput
from oskill import RunbookMatchResult, SynthesizedResult

def test_propose_action_plan_fingerprint_stability():
    config = ProposeActionPlanConfig(root_cause_hash="rc1", plugin_marketplace_version="v1")
    input_data = ProposeActionPlanInput(root_cause={}, available_plugins=[])
    from omodul.propose_action_plan import compute_fingerprint_for_propose_action_plan
    fp1 = compute_fingerprint_for_propose_action_plan(config, input_data)
    fp2 = compute_fingerprint_for_propose_action_plan(config, input_data)
    assert fp1 == fp2

def test_propose_action_plan_completed_with_plugin_match(tmp_path):
    config = ProposeActionPlanConfig(root_cause_hash="rc1", plugin_marketplace_version="v1")
    input_data = ProposeActionPlanInput(root_cause={"root_cause_hypothesis": "DB full"}, available_plugins=[])
    
    with patch("obase.ProviderRegistry.get") as mock_reg_get:
        mock_reg_get.return_value = MagicMock()
        with patch("omodul.propose_action_plan.runbook_match") as mock_match:
            mock_match.return_value = RunbookMatchResult(
                matched_plugin={"name": "cleanup", "steps": [{"step": 1, "action": "delete logs"}], "risk": "low"},
                match_score=0.95
            )
            with patch("omodul.propose_action_plan.retrieve_and_synthesize") as mock_synth:
                mock_synth.return_value = SynthesizedResult(retrieved_docs=[], synthesized_answer="...", confidence=0.5)
                
                result = propose_action_plan(config, input_data, tmp_path)
                assert result["status"] == "completed"
                assert result["findings"].matched_plugin["name"] == "cleanup"
                assert result["findings"].required_approval_level == "auto"

def test_propose_action_plan_completed_with_llm_fallback(tmp_path):
    config = ProposeActionPlanConfig(root_cause_hash="rc1", plugin_marketplace_version="v1")
    input_data = ProposeActionPlanInput(root_cause={"root_cause_hypothesis": "Unknown Bug"}, available_plugins=[])
    
    with patch("obase.ProviderRegistry.get") as mock_reg_get:
        mock_reg_get.return_value = MagicMock()
        with patch("omodul.propose_action_plan.runbook_match") as mock_match:
            mock_match.return_value = RunbookMatchResult(matched_plugin=None)
            with patch("omodul.propose_action_plan.retrieve_and_synthesize") as mock_synth:
                mock_synth.return_value = SynthesizedResult(
                    retrieved_docs=[], synthesized_answer="Custom Fix", confidence=0.8
                )
                
                result = propose_action_plan(config, input_data, tmp_path)
                assert result["findings"].matched_plugin is None
                assert result["findings"].action_plan[0]["action"] == "Custom Fix"
                assert result["findings"].required_approval_level == "user_approval"

def test_propose_action_plan_failure_path(tmp_path):
    config = ProposeActionPlanConfig(root_cause_hash="rc1", plugin_marketplace_version="v1")
    input_data = ProposeActionPlanInput(root_cause={}, available_plugins=[])
    
    with patch("omodul.propose_action_plan.runbook_match") as mock_match:
        mock_match.side_effect = Exception("Match error")
        
        result = propose_action_plan(config, input_data, tmp_path)
        assert result["status"] == "failed"
        assert "Match error" in result["error"]["error_message"]

def test_propose_action_plan_decision_trail(tmp_path):
    config = ProposeActionPlanConfig(root_cause_hash="rc1", plugin_marketplace_version="v1")
    input_data = ProposeActionPlanInput(root_cause={}, available_plugins=[])
    
    with patch("obase.ProviderRegistry.get") as mock_reg_get:
        mock_reg_get.return_value = MagicMock()
        with patch("omodul.propose_action_plan.runbook_match") as mock_match:
            mock_match.return_value = RunbookMatchResult()
            with patch("omodul.propose_action_plan.retrieve_and_synthesize") as mock_synth:
                mock_synth.return_value = SynthesizedResult(retrieved_docs=[], synthesized_answer="...", confidence=0.5)
                
                result = propose_action_plan(config, input_data, tmp_path)
                assert len(result["decision_trail"]["steps"]) >= 2

def test_propose_action_plan_fingerprint_changes_whitelist():
    config1 = ProposeActionPlanConfig(root_cause_hash="rc1", plugin_marketplace_version="v1")
    config2 = ProposeActionPlanConfig(root_cause_hash="rc2", plugin_marketplace_version="v1")
    input_data = ProposeActionPlanInput(root_cause={}, available_plugins=[])
    from omodul.propose_action_plan import compute_fingerprint_for_propose_action_plan
    assert compute_fingerprint_for_propose_action_plan(config1, input_data) != compute_fingerprint_for_propose_action_plan(config2, input_data)

def test_propose_action_plan_invariant_outside_whitelist():
    config1 = ProposeActionPlanConfig(root_cause_hash="rc1", plugin_marketplace_version="v1", budget_usd=5.0)
    config2 = ProposeActionPlanConfig(root_cause_hash="rc1", plugin_marketplace_version="v1", budget_usd=10.0)
    input_data = ProposeActionPlanInput(root_cause={}, available_plugins=[])
    from omodul.propose_action_plan import compute_fingerprint_for_propose_action_plan
    assert compute_fingerprint_for_propose_action_plan(config1, input_data) == compute_fingerprint_for_propose_action_plan(config2, input_data)

def test_propose_action_plan_report(tmp_path):
    config = ProposeActionPlanConfig(root_cause_hash="rc1", plugin_marketplace_version="v1")
    input_data = ProposeActionPlanInput(root_cause={}, available_plugins=[])
    
    with patch("obase.ProviderRegistry.get") as mock_reg_get:
        mock_reg_get.return_value = MagicMock()
        with patch("omodul.propose_action_plan._stage_runbook_match") as mock_m:
            mock_m.return_value = RunbookMatchResult()
            with patch("omodul.propose_action_plan._stage_synthesize_plan") as mock_s:
                mock_s.return_value = SynthesizedResult(retrieved_docs=[], synthesized_answer="Fix", confidence=1.0)
                
                result = propose_action_plan(config, input_data, tmp_path)
                assert result["report_path"].exists()

def test_propose_action_plan_on_step_forwarding(tmp_path):
    config = ProposeActionPlanConfig(root_cause_hash="rc1", plugin_marketplace_version="v1")
    input_data = ProposeActionPlanInput(root_cause={}, available_plugins=[])
    steps = []
    def on_step(s): steps.append(s)
    
    with patch("obase.ProviderRegistry.get") as mock_reg_get:
        mock_reg_get.return_value = MagicMock()
        with patch("omodul.propose_action_plan.runbook_match") as mock_match:
            mock_match.return_value = RunbookMatchResult()
            with patch("omodul.propose_action_plan.retrieve_and_synthesize") as mock_synth:
                mock_synth.return_value = SynthesizedResult(retrieved_docs=[], synthesized_answer="...", confidence=0.5)
                propose_action_plan(config, input_data, tmp_path, on_step=on_step)
                assert len(steps) >= 2

def test_propose_action_plan_no_user_id_in_signature():
    import inspect
    sig = inspect.signature(propose_action_plan)
    assert "user_id" not in sig.parameters

def test_propose_action_plan_approval_levels(tmp_path):
    config = ProposeActionPlanConfig(root_cause_hash="rc1", plugin_marketplace_version="v1")
    input_data = ProposeActionPlanInput(root_cause={}, available_plugins=[])
    
    from omodul.propose_action_plan import _stage_finalize_findings
    
    # Low score match -> user_approval
    m1 = RunbookMatchResult(matched_plugin={"risk": "low"}, match_score=0.7)
    s1 = SynthesizedResult(retrieved_docs=[], synthesized_answer="...", confidence=0.5)
    f1 = _stage_finalize_findings(m1, s1)
    assert f1.required_approval_level == "user_approval"
    
    # High score match + low risk -> auto
    m2 = RunbookMatchResult(matched_plugin={"risk": "low"}, match_score=0.95)
    f2 = _stage_finalize_findings(m2, s1)
    assert f2.required_approval_level == "auto"
    
    # High score match + high risk -> user_approval
    m3 = RunbookMatchResult(matched_plugin={"risk": "high"}, match_score=0.95)
    f3 = _stage_finalize_findings(m3, s1)
    assert f3.required_approval_level == "user_approval"
