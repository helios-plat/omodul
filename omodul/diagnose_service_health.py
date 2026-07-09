import json
import traceback
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, Literal

from obase.cost_tracker import CostTracker
from pydantic import BaseModel

from obase.docker import docker_inspect
from oprim import system_cpu_usage, system_ram_usage
from oprim._network import network_http_health  # v3 not top-level
from oskill import classify_signal, compute_severity_score, diagnose_pattern_match

from omodul._base_config import BaseConfig
from omodul._decision_trail import build_decision_trail, record_step
from omodul._fingerprint import compute_fingerprint
from omodul._report import write_markdown_report
from omodul._runtime import _current_cost_tracker


class DiagnoseServiceHealthConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "diagnose_service_health"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {"service_url", "container_name"}
    service_url: str
    container_name: str = ""
    health_timeout_sec: int = 5


class DiagnoseServiceHealthInput(BaseModel):
    expected_status: int = 200
    health_retries: int = 2


class DiagnoseServiceHealthFindings(BaseModel):
    http_healthy: bool
    http_status_code: int | None
    container_running: bool | None
    container_status: str | None
    cpu_used_percent: float
    ram_used_percent: float
    signal_class: str
    pattern_name: str | None
    severity_score: float
    severity_label: str
    needs_deep_investigation: bool
    recommendation: str


def diagnose_service_health(
    config: DiagnoseServiceHealthConfig,
    input_data: DiagnoseServiceHealthInput,
    output_dir: Path,
    *,
    on_step: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """服务健康综合诊断 — HTTP + container + resource → oskill signal analysis.

    B option: returns needs_deep_investigation flag instead of calling oservice,
    preserving oservice → omodul dependency direction.
    """
    started_at = datetime.now(UTC)
    fingerprint = compute_fingerprint(config, input_data)
    cost_tracker = CostTracker(budget_usd=config.budget_usd)
    trail_steps: list[dict[str, Any]] = []
    error_info = None
    status: Literal["completed", "failed"] = "completed"
    findings = None

    token = _current_cost_tracker.set(cost_tracker)
    try:
        # Stage 1: HTTP health probe
        http_healthy, http_status = _stage_http_check(config, input_data, trail_steps, on_step)

        # Stage 2: container inspect (optional — skip if no container_name)
        container_running, container_status = _stage_container_check(config, trail_steps, on_step)

        # Stage 3: system resource metrics
        cpu_pct, ram_pct = _stage_system_resources(trail_steps, on_step, started_at)

        # Stage 4: oskill analysis
        signal: dict[str, Any] = {
            "message": (
                f"service http={'ok' if http_healthy else 'fail'} "
                f"container={'running' if container_running else 'stopped/unknown'} "
                f"cpu={cpu_pct:.0f}% ram={ram_pct:.0f}%"
            ),
            "cpu_used_percent": cpu_pct,
            "resource_used_percent": max(cpu_pct, ram_pct),
            "error_rate": 0.0 if http_healthy else 1.0,
        }
        if not http_healthy:
            signal["message"] += " HTTP health check failed"

        classification = classify_signal(signal=signal)
        pattern_result = diagnose_pattern_match(signal=signal)
        severity_result = compute_severity_score(signal=signal)

        record_step(
            trail_steps=trail_steps,
            on_step=on_step,
            layer="oskill",
            callable_name="classify_signal+diagnose_pattern_match+compute_severity_score",
            inputs_summary={"http_healthy": http_healthy, "cpu_pct": cpu_pct, "ram_pct": ram_pct},
            outputs_summary={
                "signal_class": classification.signal_class,
                "pattern": pattern_result.pattern_name,
                "severity": severity_result.label,
            },
            started_at=started_at,
        )

        needs_deep = (
            not http_healthy
            or severity_result.label in ("critical", "high")
            or container_running is False
        )
        recommendation = (
            f"HTTP: {'healthy' if http_healthy else 'UNHEALTHY'}. "
            f"Container: {container_status or 'unknown'}. "
            f"CPU={cpu_pct:.0f}% RAM={ram_pct:.0f}%. "
            f"Severity: {severity_result.label}. "
            + ("Escalate to agentic investigation." if needs_deep else "Service appears healthy.")
        )

        findings = DiagnoseServiceHealthFindings(
            http_healthy=http_healthy,
            http_status_code=http_status,
            container_running=container_running,
            container_status=container_status,
            cpu_used_percent=round(cpu_pct, 2),
            ram_used_percent=round(ram_pct, 2),
            signal_class=classification.signal_class,
            pattern_name=pattern_result.pattern_name,
            severity_score=severity_result.score,
            severity_label=severity_result.label,
            needs_deep_investigation=needs_deep,
            recommendation=recommendation,
        )

    except Exception as e:
        error_info = {
            "error_class": type(e).__name__,
            "error_message": str(e),
            "traceback": traceback.format_exc(),
        }
        status = "failed"
    finally:
        _current_cost_tracker.reset(token)

    decision_trail = build_decision_trail(
        fingerprint=fingerprint,
        config=config,
        input_data=input_data,
        trail_steps=trail_steps,
        cost_tracker=cost_tracker,
        started_at=started_at,
        status=status,
        error=error_info,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "decision_trail.json").write_text(
        json.dumps(decision_trail, indent=2, ensure_ascii=False, default=str)
    )

    report_path = write_markdown_report(
        output_dir=output_dir,
        omodul_name=config._omodul_name,
        fingerprint=fingerprint,
        config=config,
        findings=findings,
        decision_trail=decision_trail,
        cost_tracker=cost_tracker,
        status=status,
    )

    return {
        "findings": findings,
        "fingerprint": fingerprint,
        "decision_trail": decision_trail,
        "report_path": report_path,
        "cost_usd": cost_tracker.total_usd,
        "status": status,
        "error": error_info,
    }


def _stage_http_check(
    config: DiagnoseServiceHealthConfig,
    input_data: DiagnoseServiceHealthInput,
    trail_steps: list[dict[str, Any]],
    on_step: Callable[[dict[str, Any]], None] | None,
) -> tuple[bool, int | None]:
    started_at = datetime.now(UTC)
    healthy = False
    status_code = None

    for _ in range(max(1, input_data.health_retries)):
        try:
            hc = network_http_health(url=config.service_url, timeout_sec=config.health_timeout_sec)
            status_code = hc.status_code
            if hc.healthy and (hc.status_code or 0) == input_data.expected_status:
                healthy = True
                break
        except Exception:
            pass

    record_step(
        trail_steps=trail_steps,
        on_step=on_step,
        layer="oprim",
        callable_name="network_http_health",
        inputs_summary={"url": config.service_url},
        outputs_summary={"healthy": healthy, "status_code": status_code},
        started_at=started_at,
    )
    return healthy, status_code


def _stage_container_check(
    config: DiagnoseServiceHealthConfig,
    trail_steps: list[dict[str, Any]],
    on_step: Callable[[dict[str, Any]], None] | None,
) -> tuple[bool | None, str | None]:
    if not config.container_name:
        return None, None

    started_at = datetime.now(UTC)
    running: bool | None = None
    container_status: str | None = None

    try:
        info = docker_inspect(container=config.container_name)
        state = info.get("State", {}) if isinstance(info, dict) else {}
        container_status = state.get("Status", "unknown")
        running = container_status == "running"
    except Exception:
        pass

    record_step(
        trail_steps=trail_steps,
        on_step=on_step,
        layer="oprim",
        callable_name="docker_inspect",
        inputs_summary={"container": config.container_name},
        outputs_summary={"running": running, "status": container_status},
        started_at=started_at,
    )
    return running, container_status


def _stage_system_resources(
    trail_steps: list[dict[str, Any]],
    on_step: Callable[[dict[str, Any]], None] | None,
    started_at: datetime,
) -> tuple[float, float]:
    try:
        cpu_pct = system_cpu_usage()
    except Exception:
        cpu_pct = 0.0

    try:
        ram = system_ram_usage()
        ram_pct = float(ram.get("used_percent", 0.0))
    except Exception:
        ram_pct = 0.0

    record_step(
        trail_steps=trail_steps,
        on_step=on_step,
        layer="oprim",
        callable_name="system_cpu_usage+system_ram_usage",
        inputs_summary={},
        outputs_summary={"cpu_pct": cpu_pct, "ram_pct": ram_pct},
        started_at=started_at,
    )
    return cpu_pct, ram_pct


def compute_fingerprint_for_diagnose_service_health(
    config: DiagnoseServiceHealthConfig, input_data: DiagnoseServiceHealthInput
) -> str:
    return compute_fingerprint(config, input_data)
