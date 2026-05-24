import sys
from unittest.mock import MagicMock, patch
sys.modules["docker"] = MagicMock()
sys.modules["docker.errors"] = MagicMock()

import pytest
from pathlib import Path
from omodul.configure_domain_for_app import configure_domain_for_app, ConfigureDomainConfig, ConfigureDomainInput

def test_domain_fingerprint_stability():
    config = ConfigureDomainConfig(domain="d.io", target_instance="i1")
    input_data = ConfigureDomainInput(target_host="1.2.3.4", target_port=80)
    from omodul.configure_domain_for_app import compute_fingerprint_for_configure_domain_for_app
    fp1 = compute_fingerprint_for_configure_domain_for_app(config, input_data)
    fp2 = compute_fingerprint_for_configure_domain_for_app(config, input_data)
    assert fp1 == fp2

def test_domain_completed_normal_path(tmp_path):
    config = ConfigureDomainConfig(domain="d.io", target_instance="i1", enable_https=True)
    input_data = ConfigureDomainInput(target_host="1.2.3.4", target_port=80)
    
    with patch("omodul.configure_domain_for_app.dns_resolve", return_value=["1.2.3.4"]):
        with patch("omodul.configure_domain_for_app.caddy_admin_reload") as mock_caddy:
            with patch("omodul.configure_domain_for_app.caddy_certificates_status") as mock_cert:
                mock_cert.return_value = [{"domain": "d.io", "status": "active"}]
                
                result = configure_domain_for_app(config, input_data, tmp_path)
                assert result["status"] == "completed"
                assert result["findings"].dns_resolved == True
                assert result["findings"].https_certificate_obtained == True

def test_domain_dns_failure(tmp_path):
    config = ConfigureDomainConfig(domain="d.io", target_instance="i1")
    input_data = ConfigureDomainInput(target_host="1.2.3.4", target_port=80)
    
    with patch("omodul.configure_domain_for_app.dns_resolve", return_value=[]):
        result = configure_domain_for_app(config, input_data, tmp_path)
        assert result["status"] == "failed"
        assert "DNS not resolved" in result["error"]["error_message"]

def test_domain_https_not_obtained(tmp_path):
    config = ConfigureDomainConfig(domain="d.io", target_instance="i1", enable_https=True)
    input_data = ConfigureDomainInput(target_host="1.2.3.4", target_port=80)
    
    with patch("omodul.configure_domain_for_app.dns_resolve", return_value=["1.2.3.4"]):
        with patch("omodul.configure_domain_for_app.caddy_admin_reload"):
            with patch("omodul.configure_domain_for_app.caddy_certificates_status", return_value=[]):
                result = configure_domain_for_app(config, input_data, tmp_path)
                assert result["status"] == "completed"
                assert result["findings"].https_certificate_obtained == False

def test_domain_failure_at_caddy(tmp_path):
    config = ConfigureDomainConfig(domain="d.io", target_instance="i1")
    input_data = ConfigureDomainInput(target_host="1.2.3.4", target_port=80)
    
    with patch("omodul.configure_domain_for_app.dns_resolve", return_value=["1.2.3.4"]):
        with patch("omodul.configure_domain_for_app.caddy_admin_reload") as mock_caddy:
            mock_caddy.side_effect = Exception("Caddy API error")
            result = configure_domain_for_app(config, input_data, tmp_path)
            assert result["status"] == "failed"

def test_domain_decision_trail(tmp_path):
    config = ConfigureDomainConfig(domain="d.io", target_instance="i1")
    input_data = ConfigureDomainInput(target_host="1.2.3.4", target_port=80)
    
    with patch("omodul.configure_domain_for_app.dns_resolve", return_value=["1.2.3.4"]):
        with patch("omodul.configure_domain_for_app.caddy_admin_reload"):
            with patch("omodul.configure_domain_for_app.caddy_certificates_status", return_value=[]):
                result = configure_domain_for_app(config, input_data, tmp_path)
                assert len(result["decision_trail"]["steps"]) >= 2

def test_domain_fingerprint_changes_whitelist():
    config1 = ConfigureDomainConfig(domain="d1.io", target_instance="i1")
    config2 = ConfigureDomainConfig(domain="d2.io", target_instance="i1")
    input_data = ConfigureDomainInput(target_host="1.2.3.4", target_port=80)
    from omodul.configure_domain_for_app import compute_fingerprint_for_configure_domain_for_app
    assert compute_fingerprint_for_configure_domain_for_app(config1, input_data) != compute_fingerprint_for_configure_domain_for_app(config2, input_data)

def test_domain_invariant_outside_whitelist():
    config1 = ConfigureDomainConfig(domain="d.io", target_instance="i1", dns_check_timeout_sec=30)
    config2 = ConfigureDomainConfig(domain="d.io", target_instance="i1", dns_check_timeout_sec=60)
    input_data = ConfigureDomainInput(target_host="1.2.3.4", target_port=80)
    from omodul.configure_domain_for_app import compute_fingerprint_for_configure_domain_for_app
    assert compute_fingerprint_for_configure_domain_for_app(config1, input_data) == compute_fingerprint_for_configure_domain_for_app(config2, input_data)

def test_domain_report_exists(tmp_path):
    config = ConfigureDomainConfig(domain="d.io", target_instance="i1")
    input_data = ConfigureDomainInput(target_host="1.2.3.4", target_port=80)
    with patch("omodul.configure_domain_for_app.dns_resolve", return_value=["1.2.3.4"]):
        with patch("omodul.configure_domain_for_app.caddy_admin_reload"):
            result = configure_domain_for_app(config, input_data, tmp_path)
            assert result["report_path"].exists()

def test_domain_on_step_forwarding(tmp_path):
    config = ConfigureDomainConfig(domain="d.io", target_instance="i1")
    input_data = ConfigureDomainInput(target_host="1.2.3.4", target_port=80)
    steps = []
    def on_step(s): steps.append(s)
    with patch("omodul.configure_domain_for_app.dns_resolve", return_value=["1.2.3.4"]):
        with patch("omodul.configure_domain_for_app.caddy_admin_reload"):
            configure_domain_for_app(config, input_data, tmp_path, on_step=on_step)
            assert len(steps) >= 2

def test_domain_no_user_id_in_signature():
    import inspect
    sig = inspect.signature(configure_domain_for_app)
    assert "user_id" not in sig.parameters

def test_domain_https_disabled(tmp_path):
    config = ConfigureDomainConfig(domain="d.io", target_instance="i1", enable_https=False)
    input_data = ConfigureDomainInput(target_host="1.2.3.4", target_port=80)
    
    with patch("omodul.configure_domain_for_app.dns_resolve", return_value=["1.2.3.4"]):
        with patch("omodul.configure_domain_for_app.caddy_admin_reload"):
            result = configure_domain_for_app(config, input_data, tmp_path)
            assert result["findings"].https_certificate_obtained == False
