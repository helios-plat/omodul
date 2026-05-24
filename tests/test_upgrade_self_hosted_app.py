import sys
from unittest.mock import MagicMock, patch
sys.modules["docker"] = MagicMock()
sys.modules["docker.errors"] = MagicMock()

import pytest
from pathlib import Path
from omodul.upgrade_self_hosted_app import upgrade_self_hosted_app, UpgradeSelfHostedAppConfig, UpgradeSelfHostedAppInput

def test_upgrade_fingerprint_stability():
    config = UpgradeSelfHostedAppConfig(instance_name="g-prod", current_version="1.0", target_version="1.1")
    input_data = UpgradeSelfHostedAppInput(container_id="c1", new_image="g:1.1")
    from omodul.upgrade_self_hosted_app import compute_fingerprint_for_upgrade_self_hosted_app
    fp1 = compute_fingerprint_for_upgrade_self_hosted_app(config, input_data)
    fp2 = compute_fingerprint_for_upgrade_self_hosted_app(config, input_data)
    assert fp1 == fp2

def test_upgrade_completed_normal_path(tmp_path):
    config = UpgradeSelfHostedAppConfig(instance_name="g-prod", current_version="1.0", target_version="1.1")
    input_data = UpgradeSelfHostedAppInput(container_id="c1", new_image="g:1.1")
    
    with patch("omodul.upgrade_self_hosted_app.docker_image_pull") as mock_pull:
        with patch("omodul.upgrade_self_hosted_app.docker_container_stop") as mock_stop:
            with patch("omodul.upgrade_self_hosted_app.docker_container_start") as mock_start:
                result = upgrade_self_hosted_app(config, input_data, tmp_path)
                assert result["status"] == "completed"
                assert result["findings"].final_version == "1.1"
                assert result["findings"].rolled_back == False

def test_upgrade_rollback_on_health_failure(tmp_path):
    config = UpgradeSelfHostedAppConfig(instance_name="g-prod", current_version="1.0", target_version="1.1", rollback_on_failure=True)
    input_data = UpgradeSelfHostedAppInput(container_id="c1", new_image="g:1.1")
    
    with patch("omodul.upgrade_self_hosted_app.docker_image_pull"):
        with patch("omodul.upgrade_self_hosted_app.docker_container_stop"):
            with patch("omodul.upgrade_self_hosted_app.docker_container_start"):
                with patch("omodul.upgrade_self_hosted_app._stage_verify_health") as mock_verify:
                    mock_verify.return_value = False
                    
                    result = upgrade_self_hosted_app(config, input_data, tmp_path)
                    assert result["status"] == "completed" # completed with rollback
                    assert result["findings"].rolled_back == True
                    assert result["findings"].final_version == "1.0"

def test_upgrade_failure_at_pull(tmp_path):
    config = UpgradeSelfHostedAppConfig(instance_name="g-prod", current_version="1.0", target_version="1.1")
    input_data = UpgradeSelfHostedAppInput(container_id="c1", new_image="g:1.1")
    
    with patch("omodul.upgrade_self_hosted_app.docker_image_pull") as mock_pull:
        mock_pull.side_effect = Exception("Pull error")
        result = upgrade_self_hosted_app(config, input_data, tmp_path)
        assert result["status"] == "failed"

def test_upgrade_decision_trail(tmp_path):
    config = UpgradeSelfHostedAppConfig(instance_name="g-prod", current_version="1.0", target_version="1.1")
    input_data = UpgradeSelfHostedAppInput(container_id="c1", new_image="g:1.1")
    
    with patch("omodul.upgrade_self_hosted_app.docker_image_pull"):
        with patch("omodul.upgrade_self_hosted_app.docker_container_stop"):
            with patch("omodul.upgrade_self_hosted_app.docker_container_start"):
                result = upgrade_self_hosted_app(config, input_data, tmp_path)
                assert len(result["decision_trail"]["steps"]) >= 4

def test_upgrade_fingerprint_changes_whitelist():
    config1 = UpgradeSelfHostedAppConfig(instance_name="g-prod", current_version="1.0", target_version="1.1")
    config2 = UpgradeSelfHostedAppConfig(instance_name="g-prod", current_version="1.0", target_version="1.2")
    input_data = UpgradeSelfHostedAppInput(container_id="c1", new_image="g:1.1")
    from omodul.upgrade_self_hosted_app import compute_fingerprint_for_upgrade_self_hosted_app
    assert compute_fingerprint_for_upgrade_self_hosted_app(config1, input_data) != compute_fingerprint_for_upgrade_self_hosted_app(config2, input_data)

def test_upgrade_invariant_outside_whitelist():
    config1 = UpgradeSelfHostedAppConfig(instance_name="g-prod", current_version="1.0", target_version="1.1", budget_usd=5.0)
    config2 = UpgradeSelfHostedAppConfig(instance_name="g-prod", current_version="1.0", target_version="1.1", budget_usd=10.0)
    input_data = UpgradeSelfHostedAppInput(container_id="c1", new_image="g:1.1")
    from omodul.upgrade_self_hosted_app import compute_fingerprint_for_upgrade_self_hosted_app
    assert compute_fingerprint_for_upgrade_self_hosted_app(config1, input_data) == compute_fingerprint_for_upgrade_self_hosted_app(config2, input_data)

def test_upgrade_report_exists(tmp_path):
    config = UpgradeSelfHostedAppConfig(instance_name="g-prod", current_version="1.0", target_version="1.1")
    input_data = UpgradeSelfHostedAppInput(container_id="c1", new_image="g:1.1")
    with patch("omodul.upgrade_self_hosted_app.docker_image_pull"):
        with patch("omodul.upgrade_self_hosted_app.docker_container_stop"):
            with patch("omodul.upgrade_self_hosted_app.docker_container_start"):
                result = upgrade_self_hosted_app(config, input_data, tmp_path)
                assert result["report_path"].exists()

def test_upgrade_on_step_forwarding(tmp_path):
    config = UpgradeSelfHostedAppConfig(instance_name="g-prod", current_version="1.0", target_version="1.1")
    input_data = UpgradeSelfHostedAppInput(container_id="c1", new_image="g:1.1")
    steps = []
    def on_step(s): steps.append(s)
    with patch("omodul.upgrade_self_hosted_app.docker_image_pull"):
        with patch("omodul.upgrade_self_hosted_app.docker_container_stop"):
            with patch("omodul.upgrade_self_hosted_app.docker_container_start"):
                upgrade_self_hosted_app(config, input_data, tmp_path, on_step=on_step)
                assert len(steps) >= 4

def test_upgrade_no_user_id_in_signature():
    import inspect
    sig = inspect.signature(upgrade_self_hosted_app)
    assert "user_id" not in sig.parameters

def test_upgrade_rollback_logic_step(tmp_path):
    config = UpgradeSelfHostedAppConfig(instance_name="g-prod", current_version="1.0", target_version="1.1", rollback_on_failure=True)
    input_data = UpgradeSelfHostedAppInput(container_id="c1", new_image="g:1.1")
    
    with patch("omodul.upgrade_self_hosted_app.docker_image_pull"):
        with patch("omodul.upgrade_self_hosted_app.docker_container_stop"):
            with patch("omodul.upgrade_self_hosted_app.docker_container_start"):
                with patch("omodul.upgrade_self_hosted_app._stage_verify_health", return_value=False):
                    result = upgrade_self_hosted_app(config, input_data, tmp_path)
                    assert any(s["callable"] == "rollback" for s in result["decision_trail"]["steps"])
