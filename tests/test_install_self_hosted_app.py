import sys
from unittest.mock import MagicMock, patch
sys.modules["docker"] = MagicMock()
sys.modules["docker.errors"] = MagicMock()

import pytest
from pathlib import Path
from omodul.install_self_hosted_app import install_self_hosted_app, InstallSelfHostedAppConfig, InstallSelfHostedAppInput

def test_install_fingerprint_stability():
    config = InstallSelfHostedAppConfig(app_slug="gitea", app_version="1.0", instance_name="g-prod", config_hash="h1")
    input_data = InstallSelfHostedAppInput(app_config={})
    from omodul.install_self_hosted_app import compute_fingerprint_for_install_self_hosted_app
    fp1 = compute_fingerprint_for_install_self_hosted_app(config, input_data)
    fp2 = compute_fingerprint_for_install_self_hosted_app(config, input_data)
    assert fp1 == fp2

def test_install_completed_normal_path(tmp_path):
    config = InstallSelfHostedAppConfig(app_slug="gitea", app_version="1.0", instance_name="g-prod", config_hash="h1", domain="g.io")
    input_data = InstallSelfHostedAppInput(app_config={"ports": {"80/tcp": 3000}})
    
    with patch("omodul.install_self_hosted_app.docker_image_pull", return_value={"Id": "sha256:image"}):
        with patch("omodul.install_self_hosted_app.docker_container_start", return_value={}):
            with patch("omodul.install_self_hosted_app.docker_container_inspect", return_value={"Id": "c1", "State": {"Running": True}}):
                with patch("omodul.install_self_hosted_app.http_health_probe", return_value={"healthy": True}):
                    with patch("omodul.install_self_hosted_app.caddy_admin_reload", return_value={}):
                        result = install_self_hosted_app(config, input_data, tmp_path)
                        assert result["status"] == "completed"
                        assert result["findings"].container_id == "c1"
                        assert result["findings"].https_active == True

def test_install_without_domain(tmp_path):
    config = InstallSelfHostedAppConfig(app_slug="gitea", app_version="1.0", instance_name="g-prod", config_hash="h1", domain=None)
    input_data = InstallSelfHostedAppInput(app_config={})
    
    with patch("omodul.install_self_hosted_app.docker_image_pull", return_value={"Id": "img"}):
        with patch("omodul.install_self_hosted_app.docker_container_start", return_value={}):
            with patch("omodul.install_self_hosted_app.docker_container_inspect", return_value={"Id": "c1", "State": {"Running": True}}):
                with patch("omodul.install_self_hosted_app.http_health_probe", return_value={"healthy": True}):
                    result = install_self_hosted_app(config, input_data, tmp_path)
                    assert result["status"] == "completed"
                    assert result["findings"].domain is None
                    assert result["findings"].https_active == False

def test_install_pull_failure(tmp_path):
    config = InstallSelfHostedAppConfig(app_slug="gitea", app_version="1.0", instance_name="g-prod", config_hash="h1")
    input_data = InstallSelfHostedAppInput(app_config={})
    
    with patch("omodul.install_self_hosted_app.docker_image_pull") as mock_pull:
        mock_pull.side_effect = Exception("Registry unreachable")
        
        result = install_self_hosted_app(config, input_data, tmp_path)
        assert result["status"] == "failed"
        assert "Registry unreachable" in result["error"]["error_message"]

def test_install_health_unhealthy(tmp_path):
    config = InstallSelfHostedAppConfig(app_slug="gitea", app_version="1.0", instance_name="g-prod", config_hash="h1")
    input_data = InstallSelfHostedAppInput(app_config={})
    
    with patch("omodul.install_self_hosted_app.docker_image_pull", return_value={"Id": "img"}):
        with patch("omodul.install_self_hosted_app.docker_container_start", return_value={}):
            with patch("omodul.install_self_hosted_app.docker_container_inspect", return_value={"Id": "c1", "State": {"Running": True}}):
                with patch("omodul.install_self_hosted_app.http_health_probe", return_value={"healthy": False}):
                    result = install_self_hosted_app(config, input_data, tmp_path)
                    assert result["status"] == "completed"
                    assert result["findings"].health_status == "unhealthy"

def test_install_decision_trail(tmp_path):
    config = InstallSelfHostedAppConfig(app_slug="a", app_version="1", instance_name="n", config_hash="h")
    input_data = InstallSelfHostedAppInput(app_config={})
    
    with patch("omodul.install_self_hosted_app.docker_image_pull", return_value={}):
        with patch("omodul.install_self_hosted_app.docker_container_start", return_value={}):
            with patch("omodul.install_self_hosted_app.docker_container_inspect", return_value={"Id": "c1", "State": {"Running": True}}):
                with patch("omodul.install_self_hosted_app.http_health_probe", return_value={"healthy": True}):
                    result = install_self_hosted_app(config, input_data, tmp_path)
                    assert len(result["decision_trail"]["steps"]) >= 3

def test_install_fingerprint_changes_whitelist():
    config1 = InstallSelfHostedAppConfig(app_slug="a", app_version="1", instance_name="n", config_hash="h1")
    config2 = InstallSelfHostedAppConfig(app_slug="a", app_version="1", instance_name="n", config_hash="h2")
    input_data = InstallSelfHostedAppInput(app_config={})
    from omodul.install_self_hosted_app import compute_fingerprint_for_install_self_hosted_app
    assert compute_fingerprint_for_install_self_hosted_app(config1, input_data) != compute_fingerprint_for_install_self_hosted_app(config2, input_data)

def test_install_report_exists(tmp_path):
    config = InstallSelfHostedAppConfig(app_slug="a", app_version="1", instance_name="n", config_hash="h")
    input_data = InstallSelfHostedAppInput(app_config={})
    with patch("omodul.install_self_hosted_app.docker_image_pull", return_value={}):
        with patch("omodul.install_self_hosted_app.docker_container_start", return_value={}):
            with patch("omodul.install_self_hosted_app.docker_container_inspect", return_value={"Id": "c1", "State": {"Running": True}}):
                with patch("omodul.install_self_hosted_app.http_health_probe", return_value={"healthy": True}):
                    result = install_self_hosted_app(config, input_data, tmp_path)
                    assert result["report_path"].exists()

def test_install_on_step_forwarding(tmp_path):
    config = InstallSelfHostedAppConfig(app_slug="a", app_version="1", instance_name="n", config_hash="h")
    input_data = InstallSelfHostedAppInput(app_config={})
    steps = []
    def on_step(s): steps.append(s)
    with patch("omodul.install_self_hosted_app.docker_image_pull", return_value={}):
        with patch("omodul.install_self_hosted_app.docker_container_start", return_value={}):
            with patch("omodul.install_self_hosted_app.docker_container_inspect", return_value={"Id": "c1", "State": {"Running": True}}):
                with patch("omodul.install_self_hosted_app.http_health_probe", return_value={"healthy": True}):
                    install_self_hosted_app(config, input_data, tmp_path, on_step=on_step)
                    assert len(steps) >= 3

def test_install_no_user_id_in_signature():
    import inspect
    sig = inspect.signature(install_self_hosted_app)
    assert "user_id" not in sig.parameters

def test_install_caddy_failure_still_completed_partial(tmp_path):
    config = InstallSelfHostedAppConfig(app_slug="a", app_version="1", instance_name="n", config_hash="h", domain="d.io")
    input_data = InstallSelfHostedAppInput(app_config={})
    
    with patch("omodul.install_self_hosted_app.docker_image_pull", return_value={}):
        with patch("omodul.install_self_hosted_app.docker_container_start", return_value={}):
            with patch("omodul.install_self_hosted_app.docker_container_inspect", return_value={"Id": "c1", "State": {"Running": True}}):
                with patch("omodul.install_self_hosted_app.http_health_probe", return_value={"healthy": True}):
                    with patch("omodul.install_self_hosted_app.caddy_admin_reload") as mock_caddy:
                        mock_caddy.side_effect = Exception("Caddy down")
                        result = install_self_hosted_app(config, input_data, tmp_path)
                        assert result["status"] == "completed" 
                        assert result["findings"].https_active == False
