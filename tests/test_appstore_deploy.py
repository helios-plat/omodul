import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from omodul.appstore_deploy import AppstoreDeployConfig, appstore_deploy, compute_fingerprint_for


@pytest.fixture
def mock_catalog(tmp_path):
    catalog = {
        "templates": [
            {
                "id": "app1",
                "compose_template": "services:\n  web:\n    image: nginx:{{version}}\n",
                "caddy": {
                    "route_config": {"handle": [{"handler": "reverse_proxy", "upstreams": [{"dial": "web:80"}]}]}
                }
            }
        ]
    }
    path = tmp_path / "catalog.yaml"
    path.write_text(yaml.dump(catalog))
    return str(path)


@patch("omodul.appstore_deploy.docker_image_pull")
@patch("omodul.appstore_deploy.compose_up")
@patch("omodul.appstore_deploy.caddy_admin_post")
def test_appstore_deploy_happy(mock_caddy, mock_up, mock_pull, mock_catalog, tmp_path):
    mock_up.return_value = {"started_services": ["web"], "stdout": "ok", "stderr": ""}
    mock_pull.return_value = {"status": "pulled"}
    mock_caddy.return_value = {"status": "ok"}

    config = AppstoreDeployConfig(
        template_id="app1",
        catalog_path=mock_catalog,
        user_params={"version": "1.2.3"},
        target_install_dir=str(tmp_path / "install"),
    )

    output_dir = tmp_path / "output"
    res = appstore_deploy(config, None, output_dir)

    assert res["status"] == "completed"
    assert res["fingerprint"] is not None
    assert (tmp_path / "install" / "docker-compose.yml").exists()
    
    # Check rendered content
    compose_content = (tmp_path / "install" / "docker-compose.yml").read_text()
    assert "image: nginx:1.2.3" in compose_content
    
    assert len(res["decision_trail"]["steps"]) >= 6
    assert mock_pull.call_count == 1
    assert mock_up.called
    assert mock_caddy.called


def test_appstore_deploy_catalog_not_found(tmp_path):
    config = AppstoreDeployConfig(
        template_id="app1",
        catalog_path="missing.yaml",
        target_install_dir=str(tmp_path / "install"),
    )
    res = appstore_deploy(config, None, tmp_path / "out")
    assert res["status"] == "failed"
    assert "FileNotFoundError" in res["error"]["type"]


def test_appstore_deploy_template_not_found(mock_catalog, tmp_path):
    config = AppstoreDeployConfig(
        template_id="ghost",
        catalog_path=mock_catalog,
        target_install_dir=str(tmp_path / "install"),
    )
    res = appstore_deploy(config, None, tmp_path / "out")
    assert res["status"] == "failed"
    assert "ValueError" in res["error"]["type"]
    assert "ghost" in res["error"]["message"]


@patch("omodul.appstore_deploy.compose_up")
def test_appstore_deploy_compose_fail(mock_up, mock_catalog, tmp_path):
    mock_up.side_effect = RuntimeError("compose died")
    config = AppstoreDeployConfig(
        template_id="app1",
        catalog_path=mock_catalog,
        target_install_dir=str(tmp_path / "install"),
    )
    res = appstore_deploy(config, None, tmp_path / "out")
    assert res["status"] == "failed"
    assert "OBaseConnectionError" in res["error"]["type"]


def test_appstore_deploy_fingerprint_stability(mock_catalog, tmp_path):
    c1 = AppstoreDeployConfig(
        template_id="app1", catalog_path=mock_catalog, user_params={"v": "1"}, target_install_dir="d"
    )
    c2 = AppstoreDeployConfig(
        template_id="app1", catalog_path=mock_catalog, user_params={"v": "1"}, target_install_dir="d"
    )
    c3 = AppstoreDeployConfig(
        template_id="app1", catalog_path=mock_catalog, user_params={"v": "2"}, target_install_dir="d"
    )
    
    f1 = compute_fingerprint_for(c1, None)
    f2 = compute_fingerprint_for(c2, None)
    f3 = compute_fingerprint_for(c3, None)
    
    assert f1 == f2
    assert f1 != f3


def test_appstore_deploy_fingerprint_ignored_fields(mock_catalog):
    c1 = AppstoreDeployConfig(
        template_id="app1", catalog_path=mock_catalog, target_install_dir="d", wait_health_seconds=10
    )
    c2 = AppstoreDeployConfig(
        template_id="app1", catalog_path=mock_catalog, target_install_dir="d", wait_health_seconds=60
    )
    assert compute_fingerprint_for(c1, None) == compute_fingerprint_for(c2, None)


@patch("omodul.appstore_deploy.compose_up")
def test_appstore_deploy_no_pull(mock_up, mock_catalog, tmp_path):
    mock_up.return_value = {}
    config = AppstoreDeployConfig(
        template_id="app1",
        catalog_path=mock_catalog,
        target_install_dir=str(tmp_path / "install"),
        pull_images=False
    )
    res = appstore_deploy(config, None, tmp_path / "out")
    # Step 3 (pull) should be skipped
    stages = [s["callable"] for s in res["decision_trail"]["steps"]]
    assert "docker_image_pull" not in stages


@patch("omodul.appstore_deploy.compose_up")
def test_appstore_deploy_no_caddy(mock_up, tmp_path):
    mock_up.return_value = {}
    # Template without caddy
    catalog = {"templates": [{"id": "simple", "compose_template": "..."}]}
    cat_path = tmp_path / "cat.yaml"
    cat_path.write_text(yaml.dump(catalog))
    
    config = AppstoreDeployConfig(
        template_id="simple",
        catalog_path=str(cat_path),
        target_install_dir=str(tmp_path / "install"),
    )
    res = appstore_deploy(config, None, tmp_path / "out")
    stages = [s["callable"] for s in res["decision_trail"]["steps"]]
    assert "caddy_admin_post" not in stages


def test_appstore_deploy_on_step(mock_catalog, tmp_path):
    config = AppstoreDeployConfig(
        template_id="app1", catalog_path=mock_catalog, target_install_dir="d"
    )
    steps = []
    def callback(s): steps.append(s)

    with patch("omodul.appstore_deploy.compose_up", return_value={}):
        with patch("omodul.appstore_deploy.docker_image_pull", return_value={}):
            appstore_deploy(config, None, tmp_path / "out", on_step=callback)

    assert len(steps) >= 3


def test_appstore_deploy_decision_trail_fields(mock_catalog, tmp_path):
    config = AppstoreDeployConfig(
        template_id="app1", catalog_path=mock_catalog, target_install_dir="d"
    )
    with patch("omodul.appstore_deploy.compose_up", return_value={}):
        res = appstore_deploy(config, None, tmp_path / "out")
    
    trail = res["decision_trail"]
    assert "fingerprint" in trail
    assert "omodul_name" in trail
    assert "steps" in trail
    assert "cost_breakdown" in trail
