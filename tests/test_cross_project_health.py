import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from omodul.cross_project_health_aggregate import (
    CrossProjectHealthConfig,
    compute_fingerprint_for,
    cross_project_health_aggregate,
)


@patch("omodul.cross_project_health_aggregate.docker_container_list")
@patch("omodul.cross_project_health_aggregate.http_health_probe")
def test_cross_project_health_happy(mock_probe, mock_list, tmp_path):
    # Mock containers
    c1 = MagicMock(container_id="c1", name="svc1", labels={"aegis.project": "p1"})
    c2 = MagicMock(container_id="c2", name="svc2", labels={"aegis.project": "p1"})
    c3 = MagicMock(container_id="c3", name="svc3", labels={"aegis.project": "p2"})
    mock_list.return_value = [c1, c2, c3]

    # Mock health results
    def probe_side_effect(url):
        if "svc1" in url: return MagicMock(healthy=True)
        if "svc2" in url: return MagicMock(healthy=True)
        if "svc3" in url: return MagicMock(healthy=True)
        return MagicMock(healthy=False)
    
    mock_probe.side_effect = probe_side_effect

    config = CrossProjectHealthConfig(label_key="aegis.project")
    res = cross_project_health_aggregate(config, None, tmp_path / "out")

    assert res["status"] == "completed"
    agg = res["findings"]["project_aggregation"]
    assert agg["p1"]["status"] == "ok"
    assert agg["p2"]["status"] == "ok"
    assert (tmp_path / "out" / "report.md").exists()


@patch("omodul.cross_project_health_aggregate.docker_container_list")
@patch("omodul.cross_project_health_aggregate.http_health_probe")
def test_cross_project_health_degraded(mock_probe, mock_list, tmp_path):
    c1 = MagicMock(container_id="c1", name="svc1", labels={"aegis.project": "p1"})
    c2 = MagicMock(container_id="c2", name="svc2", labels={"aegis.project": "p1"})
    mock_list.return_value = [c1, c2]

    def probe_side_effect(url):
        if "svc1" in url: return MagicMock(healthy=True)
        return MagicMock(healthy=False)
    
    mock_probe.side_effect = probe_side_effect

    res = cross_project_health_aggregate(CrossProjectHealthConfig(), None, tmp_path / "out")
    agg = res["findings"]["project_aggregation"]
    assert agg["p1"]["status"] == "degraded"


@patch("omodul.cross_project_health_aggregate.docker_container_list")
@patch("omodul.cross_project_health_aggregate.http_health_probe")
def test_cross_project_health_down(mock_probe, mock_list, tmp_path):
    c1 = MagicMock(container_id="c1", name="svc1", labels={"aegis.project": "p1"})
    mock_list.return_value = [c1]
    mock_probe.return_value = MagicMock(healthy=False)

    res = cross_project_health_aggregate(CrossProjectHealthConfig(), None, tmp_path / "out")
    agg = res["findings"]["project_aggregation"]
    assert agg["p1"]["status"] == "down"


@patch("omodul.cross_project_health_aggregate.docker_container_list")
def test_cross_project_health_empty(mock_list, tmp_path):
    mock_list.return_value = []
    res = cross_project_health_aggregate(CrossProjectHealthConfig(), None, tmp_path / "out")
    assert res["findings"]["project_aggregation"] == {}


def test_cross_project_health_fingerprint():
    c1 = CrossProjectHealthConfig(label_key="proj", time_window_seconds=10)
    c2 = CrossProjectHealthConfig(label_key="proj", time_window_seconds=10)
    c3 = CrossProjectHealthConfig(label_key="proj", time_window_seconds=20)
    
    assert compute_fingerprint_for(c1, None) == compute_fingerprint_for(c2, None)
    assert compute_fingerprint_for(c1, None) != compute_fingerprint_for(c3, None)


@patch("omodul.cross_project_health_aggregate.docker_container_list")
@patch("omodul.cross_project_health_aggregate.http_health_probe")
def test_cross_project_health_probe_error(mock_probe, mock_list, tmp_path):
    c1 = MagicMock(container_id="c1", name="svc1", labels={"aegis.project": "p1"})
    mock_list.return_value = [c1]
    mock_probe.side_effect = Exception("network error")

    res = cross_project_health_aggregate(CrossProjectHealthConfig(), None, tmp_path / "out")
    agg = res["findings"]["project_aggregation"]
    assert agg["p1"]["status"] == "down"


def test_cross_project_health_on_step(tmp_path):
    steps = []
    def callback(s): steps.append(s)
    
    with patch("omodul.cross_project_health_aggregate.docker_container_list", return_value=[]):
        cross_project_health_aggregate(CrossProjectHealthConfig(), None, tmp_path / "out", on_step=callback)
    
    assert len(steps) >= 2


@patch("omodul.cross_project_health_aggregate.docker_container_list", side_effect=RuntimeError("docker down"))
def test_cross_project_health_failed(mock_list, tmp_path):
    res = cross_project_health_aggregate(CrossProjectHealthConfig(), None, tmp_path / "out")
    assert res["status"] == "failed"
    assert res["error"]["type"] == "RuntimeError"


@patch("omodul.cross_project_health_aggregate.docker_container_list")
@patch("omodul.cross_project_health_aggregate.http_health_probe")
def test_cross_project_health_multi_project(mock_probe, mock_list, tmp_path):
    c1 = MagicMock(container_id="c1", name="p1-s1", labels={"aegis.project": "p1"})
    c2 = MagicMock(container_id="c2", name="p2-s1", labels={"aegis.project": "p2"})
    mock_list.return_value = [c1, c2]
    mock_probe.return_value = MagicMock(healthy=True)
    
    res = cross_project_health_aggregate(CrossProjectHealthConfig(), None, tmp_path / "out")
    agg = res["findings"]["project_aggregation"]
    assert "p1" in agg
    assert "p2" in agg


@patch("omodul.cross_project_health_aggregate.docker_container_list", return_value=[])
def test_cross_project_health_report_content(mock_list, tmp_path):
    res = cross_project_health_aggregate(CrossProjectHealthConfig(), None, tmp_path / "out")
    report = Path(res["report_path"]).read_text()
    assert "# Cross Project Health Aggregation" in report
