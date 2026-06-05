"""B4 tests: diagnose_queue_health, diagnose_connection_pool, diagnose_service_health."""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from omodul.diagnose_queue_health import (
    DiagnoseQueueHealthConfig,
    DiagnoseQueueHealthFindings,
    DiagnoseQueueHealthInput,
    diagnose_queue_health,
)
from omodul.diagnose_connection_pool import (
    DiagnoseConnectionPoolConfig,
    DiagnoseConnectionPoolFindings,
    DiagnoseConnectionPoolInput,
    diagnose_connection_pool,
)
from omodul.diagnose_service_health import (
    DiagnoseServiceHealthConfig,
    DiagnoseServiceHealthFindings,
    DiagnoseServiceHealthInput,
    diagnose_service_health,
)


# ─── helpers ────────────────────────────────────────────────────────────────


def _tmp_dir():
    return Path(tempfile.mkdtemp())


# ─── diagnose_queue_health ───────────────────────────────────────────────────


class TestDiagnoseQueueHealth:
    def _config(self, depth_threshold=1000):
        return DiagnoseQueueHealthConfig(
            mgmt_url="http://localhost:15672",
            depth_threshold=depth_threshold,
            queue_names_hash="testhash",
        )

    def _input(self, names=None):
        return DiagnoseQueueHealthInput(queue_names=names or ["jobs", "emails"])

    def test_completed_status_on_success(self):
        with (
            patch("omodul.diagnose_queue_health.rabbitmq_queue_depth", return_value=50),
            patch("omodul.diagnose_queue_health.rabbitmq_consumer_count", return_value=3),
        ):
            result = diagnose_queue_health(self._config(), self._input(), _tmp_dir())
            assert result["status"] == "completed"
            assert result["error"] is None

    def test_findings_type_on_success(self):
        with (
            patch("omodul.diagnose_queue_health.rabbitmq_queue_depth", return_value=100),
            patch("omodul.diagnose_queue_health.rabbitmq_consumer_count", return_value=2),
        ):
            result = diagnose_queue_health(self._config(), self._input(), _tmp_dir())
            assert isinstance(result["findings"], DiagnoseQueueHealthFindings)

    def test_queue_stats_populated(self):
        with (
            patch("omodul.diagnose_queue_health.rabbitmq_queue_depth", return_value=200),
            patch("omodul.diagnose_queue_health.rabbitmq_consumer_count", return_value=1),
        ):
            result = diagnose_queue_health(self._config(), self._input(["q1", "q2"]), _tmp_dir())
            assert len(result["findings"].queue_stats) == 2

    def test_needs_deep_when_depth_exceeds_threshold(self):
        with (
            patch("omodul.diagnose_queue_health.rabbitmq_queue_depth", return_value=5000),
            patch("omodul.diagnose_queue_health.rabbitmq_consumer_count", return_value=0),
        ):
            result = diagnose_queue_health(
                self._config(depth_threshold=100), self._input(["q1"]), _tmp_dir()
            )
            assert result["findings"].needs_deep_investigation is True

    def test_no_deep_investigation_when_healthy(self):
        with (
            patch("omodul.diagnose_queue_health.rabbitmq_queue_depth", return_value=10),
            patch("omodul.diagnose_queue_health.rabbitmq_consumer_count", return_value=5),
        ):
            result = diagnose_queue_health(
                self._config(depth_threshold=1000), self._input(["q1"]), _tmp_dir()
            )
            assert result["findings"].needs_deep_investigation is False

    def test_decision_trail_json_written(self):
        out_dir = _tmp_dir()
        with (
            patch("omodul.diagnose_queue_health.rabbitmq_queue_depth", return_value=10),
            patch("omodul.diagnose_queue_health.rabbitmq_consumer_count", return_value=2),
        ):
            diagnose_queue_health(self._config(), self._input(), out_dir)
        trail_file = out_dir / "decision_trail.json"
        assert trail_file.exists()
        trail = json.loads(trail_file.read_text())
        assert trail["status"] == "completed"

    def test_failed_status_no_raise_on_oprim_error(self):
        with patch(
            "omodul.diagnose_queue_health.rabbitmq_queue_depth",
            side_effect=ConnectionError("mq down"),
        ):
            result = diagnose_queue_health(self._config(), self._input(), _tmp_dir())
            assert result["status"] == "failed"
            assert result["findings"] is None
            assert result["error"] is not None

    def test_decision_trail_written_on_failure(self):
        out_dir = _tmp_dir()
        with patch(
            "omodul.diagnose_queue_health.rabbitmq_queue_depth",
            side_effect=RuntimeError("boom"),
        ):
            diagnose_queue_health(self._config(), self._input(), out_dir)
        assert (out_dir / "decision_trail.json").exists()

    def test_on_step_callback_called(self):
        steps = []
        with (
            patch("omodul.diagnose_queue_health.rabbitmq_queue_depth", return_value=5),
            patch("omodul.diagnose_queue_health.rabbitmq_consumer_count", return_value=2),
        ):
            diagnose_queue_health(self._config(), self._input(), _tmp_dir(), on_step=steps.append)
        assert len(steps) >= 1

    def test_return_dict_keys_present(self):
        with (
            patch("omodul.diagnose_queue_health.rabbitmq_queue_depth", return_value=5),
            patch("omodul.diagnose_queue_health.rabbitmq_consumer_count", return_value=2),
        ):
            result = diagnose_queue_health(self._config(), self._input(), _tmp_dir())
        assert all(
            k in result
            for k in (
                "findings",
                "fingerprint",
                "decision_trail",
                "report_path",
                "cost_usd",
                "status",
                "error",
            )
        )

    def test_report_path_exists(self):
        out_dir = _tmp_dir()
        with (
            patch("omodul.diagnose_queue_health.rabbitmq_queue_depth", return_value=5),
            patch("omodul.diagnose_queue_health.rabbitmq_consumer_count", return_value=2),
        ):
            result = diagnose_queue_health(self._config(), self._input(), out_dir)
        assert result["report_path"].exists()

    def test_depth_ok_flag_set_correctly(self):
        with (
            patch("omodul.diagnose_queue_health.rabbitmq_queue_depth", return_value=999),
            patch("omodul.diagnose_queue_health.rabbitmq_consumer_count", return_value=1),
        ):
            result = diagnose_queue_health(
                self._config(depth_threshold=1000), self._input(["q1"]), _tmp_dir()
            )
            assert result["findings"].queue_stats[0].depth_ok is True

    def test_consumer_ok_false_when_no_consumers(self):
        with (
            patch("omodul.diagnose_queue_health.rabbitmq_queue_depth", return_value=0),
            patch("omodul.diagnose_queue_health.rabbitmq_consumer_count", return_value=0),
        ):
            result = diagnose_queue_health(self._config(), self._input(["lonely_q"]), _tmp_dir())
            assert result["findings"].queue_stats[0].consumer_ok is False


# ─── diagnose_connection_pool ────────────────────────────────────────────────


class TestDiagnoseConnectionPool:
    def _config(self):
        return DiagnoseConnectionPoolConfig(
            dsn="postgresql://user:pass@localhost/db",
            slow_threshold_ms=3000,
            dsn_hash="testhash",
        )

    def _input(self, active=10, max_conn=100):
        return DiagnoseConnectionPoolInput(active_connections=active, max_connections=max_conn)

    def test_completed_status_on_success(self):
        with (
            patch("omodul.diagnose_connection_pool.postgres_long_running_queries", return_value=[]),
            patch("omodul.diagnose_connection_pool.postgres_locks_status", return_value=[]),
        ):
            result = diagnose_connection_pool(self._config(), self._input(), _tmp_dir())
            assert result["status"] == "completed"

    def test_findings_type_on_success(self):
        with (
            patch("omodul.diagnose_connection_pool.postgres_long_running_queries", return_value=[]),
            patch("omodul.diagnose_connection_pool.postgres_locks_status", return_value=[]),
        ):
            result = diagnose_connection_pool(self._config(), self._input(), _tmp_dir())
            assert isinstance(result["findings"], DiagnoseConnectionPoolFindings)

    def test_slow_queries_counted(self):
        slow = [
            {"pid": 1, "duration_ms": 5000, "query": "SELECT * FROM big_table", "state": "active"},
            {"pid": 2, "duration_ms": 8000, "query": "UPDATE ...", "state": "idle in tx"},
        ]
        with (
            patch(
                "omodul.diagnose_connection_pool.postgres_long_running_queries", return_value=slow
            ),
            patch("omodul.diagnose_connection_pool.postgres_locks_status", return_value=[]),
        ):
            result = diagnose_connection_pool(self._config(), self._input(), _tmp_dir())
            assert len(result["findings"].slow_queries) == 2

    def test_lock_count_counted(self):
        locks = [{"pid": 1}, {"pid": 2}, {"pid": 3}]
        with (
            patch("omodul.diagnose_connection_pool.postgres_long_running_queries", return_value=[]),
            patch("omodul.diagnose_connection_pool.postgres_locks_status", return_value=locks),
        ):
            result = diagnose_connection_pool(self._config(), self._input(), _tmp_dir())
            assert result["findings"].lock_count == 3

    def test_connection_used_percent_computed(self):
        with (
            patch("omodul.diagnose_connection_pool.postgres_long_running_queries", return_value=[]),
            patch("omodul.diagnose_connection_pool.postgres_locks_status", return_value=[]),
        ):
            result = diagnose_connection_pool(
                self._config(), self._input(active=50, max_conn=100), _tmp_dir()
            )
            assert result["findings"].connection_used_percent == pytest.approx(50.0)

    def test_needs_deep_on_many_slow_queries(self):
        slow = [{"pid": i, "duration_ms": 6000, "query": "q", "state": "active"} for i in range(6)]
        with (
            patch(
                "omodul.diagnose_connection_pool.postgres_long_running_queries", return_value=slow
            ),
            patch("omodul.diagnose_connection_pool.postgres_locks_status", return_value=[]),
        ):
            result = diagnose_connection_pool(self._config(), self._input(), _tmp_dir())
            assert result["findings"].needs_deep_investigation is True

    def test_needs_deep_on_many_locks(self):
        locks = [{"pid": i} for i in range(15)]
        with (
            patch("omodul.diagnose_connection_pool.postgres_long_running_queries", return_value=[]),
            patch("omodul.diagnose_connection_pool.postgres_locks_status", return_value=locks),
        ):
            result = diagnose_connection_pool(self._config(), self._input(), _tmp_dir())
            assert result["findings"].needs_deep_investigation is True

    def test_decision_trail_written(self):
        out_dir = _tmp_dir()
        with (
            patch("omodul.diagnose_connection_pool.postgres_long_running_queries", return_value=[]),
            patch("omodul.diagnose_connection_pool.postgres_locks_status", return_value=[]),
        ):
            diagnose_connection_pool(self._config(), self._input(), out_dir)
        assert (out_dir / "decision_trail.json").exists()

    def test_failed_status_no_raise_on_error(self):
        with patch(
            "omodul.diagnose_connection_pool.postgres_long_running_queries",
            side_effect=RuntimeError("pg down"),
        ):
            result = diagnose_connection_pool(self._config(), self._input(), _tmp_dir())
            assert result["status"] == "failed"
            assert result["findings"] is None

    def test_trail_written_on_failure(self):
        out_dir = _tmp_dir()
        with patch(
            "omodul.diagnose_connection_pool.postgres_long_running_queries",
            side_effect=ConnectionError("timeout"),
        ):
            diagnose_connection_pool(self._config(), self._input(), out_dir)
        assert (out_dir / "decision_trail.json").exists()

    def test_on_step_called(self):
        steps = []
        with (
            patch("omodul.diagnose_connection_pool.postgres_long_running_queries", return_value=[]),
            patch("omodul.diagnose_connection_pool.postgres_locks_status", return_value=[]),
        ):
            diagnose_connection_pool(
                self._config(), self._input(), _tmp_dir(), on_step=steps.append
            )
        assert len(steps) >= 1

    def test_all_return_keys_present(self):
        with (
            patch("omodul.diagnose_connection_pool.postgres_long_running_queries", return_value=[]),
            patch("omodul.diagnose_connection_pool.postgres_locks_status", return_value=[]),
        ):
            result = diagnose_connection_pool(self._config(), self._input(), _tmp_dir())
        assert all(
            k in result
            for k in (
                "findings",
                "fingerprint",
                "decision_trail",
                "report_path",
                "cost_usd",
                "status",
                "error",
            )
        )


# ─── diagnose_service_health ─────────────────────────────────────────────────


class TestDiagnoseServiceHealth:
    def _config(self, container_name=""):
        return DiagnoseServiceHealthConfig(
            service_url="http://myapp:8080/health",
            container_name=container_name,
        )

    def _input(self):
        return DiagnoseServiceHealthInput(health_retries=1)

    def _healthy_hc(self):
        hc = MagicMock()
        hc.healthy = True
        hc.status_code = 200
        hc.error = None
        return hc

    def _unhealthy_hc(self):
        hc = MagicMock()
        hc.healthy = False
        hc.status_code = 503
        hc.error = "svc down"
        return hc

    def test_completed_status_on_success(self):
        with (
            patch(
                "omodul.diagnose_service_health.network_http_health",
                return_value=self._healthy_hc(),
            ),
            patch("omodul.diagnose_service_health.system_cpu_usage", return_value=20.0),
            patch(
                "omodul.diagnose_service_health.system_ram_usage",
                return_value={"used_percent": 40.0},
            ),
        ):
            result = diagnose_service_health(self._config(), self._input(), _tmp_dir())
            assert result["status"] == "completed"

    def test_findings_type_on_success(self):
        with (
            patch(
                "omodul.diagnose_service_health.network_http_health",
                return_value=self._healthy_hc(),
            ),
            patch("omodul.diagnose_service_health.system_cpu_usage", return_value=30.0),
            patch(
                "omodul.diagnose_service_health.system_ram_usage",
                return_value={"used_percent": 50.0},
            ),
        ):
            result = diagnose_service_health(self._config(), self._input(), _tmp_dir())
            assert isinstance(result["findings"], DiagnoseServiceHealthFindings)

    def test_http_healthy_true_when_ok(self):
        with (
            patch(
                "omodul.diagnose_service_health.network_http_health",
                return_value=self._healthy_hc(),
            ),
            patch("omodul.diagnose_service_health.system_cpu_usage", return_value=10.0),
            patch(
                "omodul.diagnose_service_health.system_ram_usage",
                return_value={"used_percent": 20.0},
            ),
        ):
            result = diagnose_service_health(self._config(), self._input(), _tmp_dir())
            assert result["findings"].http_healthy is True

    def test_http_healthy_false_when_fail(self):
        with (
            patch(
                "omodul.diagnose_service_health.network_http_health",
                return_value=self._unhealthy_hc(),
            ),
            patch("omodul.diagnose_service_health.system_cpu_usage", return_value=10.0),
            patch(
                "omodul.diagnose_service_health.system_ram_usage",
                return_value={"used_percent": 20.0},
            ),
        ):
            result = diagnose_service_health(self._config(), self._input(), _tmp_dir())
            assert result["findings"].http_healthy is False

    def test_needs_deep_when_http_unhealthy(self):
        with (
            patch(
                "omodul.diagnose_service_health.network_http_health",
                return_value=self._unhealthy_hc(),
            ),
            patch("omodul.diagnose_service_health.system_cpu_usage", return_value=5.0),
            patch(
                "omodul.diagnose_service_health.system_ram_usage",
                return_value={"used_percent": 10.0},
            ),
        ):
            result = diagnose_service_health(self._config(), self._input(), _tmp_dir())
            assert result["findings"].needs_deep_investigation is True

    def test_no_container_check_when_no_name(self):
        with (
            patch(
                "omodul.diagnose_service_health.network_http_health",
                return_value=self._healthy_hc(),
            ),
            patch("omodul.diagnose_service_health.docker_inspect") as mock_di,
            patch("omodul.diagnose_service_health.system_cpu_usage", return_value=10.0),
            patch(
                "omodul.diagnose_service_health.system_ram_usage",
                return_value={"used_percent": 10.0},
            ),
        ):
            diagnose_service_health(self._config(container_name=""), self._input(), _tmp_dir())
            mock_di.assert_not_called()

    def test_container_check_called_when_name_set(self):
        docker_info = {"State": {"Status": "running"}}
        with (
            patch(
                "omodul.diagnose_service_health.network_http_health",
                return_value=self._healthy_hc(),
            ),
            patch("omodul.diagnose_service_health.docker_inspect", return_value=docker_info),
            patch("omodul.diagnose_service_health.system_cpu_usage", return_value=10.0),
            patch(
                "omodul.diagnose_service_health.system_ram_usage",
                return_value={"used_percent": 10.0},
            ),
        ):
            result = diagnose_service_health(
                self._config(container_name="myapp"), self._input(), _tmp_dir()
            )
            assert result["findings"].container_running is True

    def test_needs_deep_when_container_stopped(self):
        docker_info = {"State": {"Status": "exited"}}
        with (
            patch(
                "omodul.diagnose_service_health.network_http_health",
                return_value=self._healthy_hc(),
            ),
            patch("omodul.diagnose_service_health.docker_inspect", return_value=docker_info),
            patch("omodul.diagnose_service_health.system_cpu_usage", return_value=5.0),
            patch(
                "omodul.diagnose_service_health.system_ram_usage",
                return_value={"used_percent": 5.0},
            ),
        ):
            result = diagnose_service_health(
                self._config(container_name="myapp"), self._input(), _tmp_dir()
            )
            assert result["findings"].needs_deep_investigation is True

    def test_failed_status_no_raise_on_error(self):
        # HTTP probe absorbs exceptions and returns healthy=False (correct behavior).
        # The module still completes; needs_deep_investigation is set True.
        with (
            patch(
                "omodul.diagnose_service_health.network_http_health",
                side_effect=RuntimeError("network down"),
            ),
            patch("omodul.diagnose_service_health.system_cpu_usage", return_value=5.0),
            patch(
                "omodul.diagnose_service_health.system_ram_usage",
                return_value={"used_percent": 5.0},
            ),
        ):
            result = diagnose_service_health(self._config(), self._input(), _tmp_dir())
            assert result["status"] == "completed"
            assert result["findings"].http_healthy is False
            assert result["findings"].needs_deep_investigation is True

    def test_decision_trail_written_on_success(self):
        out_dir = _tmp_dir()
        with (
            patch(
                "omodul.diagnose_service_health.network_http_health",
                return_value=self._healthy_hc(),
            ),
            patch("omodul.diagnose_service_health.system_cpu_usage", return_value=10.0),
            patch(
                "omodul.diagnose_service_health.system_ram_usage",
                return_value={"used_percent": 10.0},
            ),
        ):
            diagnose_service_health(self._config(), self._input(), out_dir)
        assert (out_dir / "decision_trail.json").exists()

    def test_decision_trail_written_on_failure(self):
        out_dir = _tmp_dir()
        with patch(
            "omodul.diagnose_service_health.network_http_health",
            side_effect=ConnectionError("refused"),
        ):
            diagnose_service_health(self._config(), self._input(), out_dir)
        assert (out_dir / "decision_trail.json").exists()

    def test_on_step_callback_called(self):
        steps = []
        with (
            patch(
                "omodul.diagnose_service_health.network_http_health",
                return_value=self._healthy_hc(),
            ),
            patch("omodul.diagnose_service_health.system_cpu_usage", return_value=10.0),
            patch(
                "omodul.diagnose_service_health.system_ram_usage",
                return_value={"used_percent": 10.0},
            ),
        ):
            diagnose_service_health(self._config(), self._input(), _tmp_dir(), on_step=steps.append)
        assert len(steps) >= 1

    def test_cpu_ram_metrics_in_findings(self):
        with (
            patch(
                "omodul.diagnose_service_health.network_http_health",
                return_value=self._healthy_hc(),
            ),
            patch("omodul.diagnose_service_health.system_cpu_usage", return_value=42.5),
            patch(
                "omodul.diagnose_service_health.system_ram_usage",
                return_value={"used_percent": 67.3},
            ),
        ):
            result = diagnose_service_health(self._config(), self._input(), _tmp_dir())
            assert result["findings"].cpu_used_percent == pytest.approx(42.5, abs=0.1)
            assert result["findings"].ram_used_percent == pytest.approx(67.3, abs=0.1)

    def test_system_resource_failure_does_not_crash(self):
        with (
            patch(
                "omodul.diagnose_service_health.network_http_health",
                return_value=self._healthy_hc(),
            ),
            patch(
                "omodul.diagnose_service_health.system_cpu_usage", side_effect=OSError("no psutil")
            ),
            patch(
                "omodul.diagnose_service_health.system_ram_usage", side_effect=OSError("no psutil")
            ),
        ):
            result = diagnose_service_health(self._config(), self._input(), _tmp_dir())
            assert result["status"] == "completed"
            assert result["findings"].cpu_used_percent == 0.0
