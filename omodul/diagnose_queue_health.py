import json
import traceback
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, Literal

from obase.cost_tracker import CostTracker
from pydantic import BaseModel

from oprim import rabbitmq_queue_depth, rabbitmq_consumer_count
from oskill import diagnose_pattern_match, compute_severity_score, circuit_breaker_check

from omodul._base_config import BaseConfig
from omodul._decision_trail import build_decision_trail, record_step
from omodul._fingerprint import compute_fingerprint
from omodul._report import write_markdown_report
from omodul._runtime import _current_cost_tracker


class DiagnoseQueueHealthConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "diagnose_queue_health"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {"mgmt_url", "queue_names_hash"}
    mgmt_url: str
    depth_threshold: int = 1000
    consumer_min: int = 1
    queue_names_hash: str = ""


class DiagnoseQueueHealthInput(BaseModel):
    queue_names: list[str]
    vhost: str = "/"
    timeout_sec: int = 5


class QueueStat(BaseModel):
    queue_name: str
    depth: int
    consumers: int
    depth_ok: bool
    consumer_ok: bool


class DiagnoseQueueHealthFindings(BaseModel):
    queue_stats: list[QueueStat]
    pattern_name: str | None
    pattern_confidence: float
    severity_score: float
    severity_label: str
    circuit_state: str
    needs_deep_investigation: bool
    recommendation: str


def diagnose_queue_health(
    config: DiagnoseQueueHealthConfig,
    input_data: DiagnoseQueueHealthInput,
    output_dir: Path,
    *,
    on_step: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """RabbitMQ 队列健康诊断 — oprim depth/consumer → oskill pattern/severity.

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
        # Stage 1: gather queue metrics via oprim
        queue_stats = _stage_gather_queue_metrics(config, input_data, trail_steps, on_step)

        # Stage 2: build signal for oskill
        total_depth = sum(s.depth for s in queue_stats)
        consumer_failures = sum(1 for s in queue_stats if not s.consumer_ok)
        signal = {
            "message": f"queue depth {total_depth}, {consumer_failures} queues with no consumers",
            "queue_depth": total_depth,
            "resource_used_percent": min(total_depth / max(config.depth_threshold, 1) * 100, 100),
            "error_rate": consumer_failures / max(len(queue_stats), 1),
        }

        # Stage 3: pattern match + severity (oskill pure-algo calls — no oprim, no oservice)
        pattern_result = diagnose_pattern_match(signal=signal)
        severity_result = compute_severity_score(signal=signal)

        # Stage 4: circuit breaker on queue error samples
        samples = [{"success": s.consumer_ok and s.depth_ok} for s in queue_stats]
        cb_result = circuit_breaker_check(samples=samples, current_state="closed")

        record_step(
            trail_steps=trail_steps,
            on_step=on_step,
            layer="oskill",
            callable_name="diagnose_pattern_match+compute_severity_score+circuit_breaker_check",
            inputs_summary={"queues": len(queue_stats), "total_depth": total_depth},
            outputs_summary={
                "pattern": pattern_result.pattern_name,
                "severity": severity_result.label,
                "circuit_state": cb_result.state,
            },
            started_at=started_at,
        )

        any_depth_breach = any(not s.depth_ok for s in queue_stats)
        needs_deep = (
            severity_result.label in ("critical", "high")
            or cb_result.should_trip
            or any_depth_breach
        )
        recommendation = (
            f"Queue depth={total_depth} (threshold={config.depth_threshold}). "
            f"Severity: {severity_result.label}. "
            + (
                "Escalate to agentic investigation."
                if needs_deep
                else "Monitor — within normal range."
            )
        )

        findings = DiagnoseQueueHealthFindings(
            queue_stats=queue_stats,
            pattern_name=pattern_result.pattern_name,
            pattern_confidence=pattern_result.confidence,
            severity_score=severity_result.score,
            severity_label=severity_result.label,
            circuit_state=cb_result.state,
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


def _stage_gather_queue_metrics(
    config: DiagnoseQueueHealthConfig,
    input_data: DiagnoseQueueHealthInput,
    trail_steps: list[dict[str, Any]],
    on_step: Callable[[dict[str, Any]], None] | None,
) -> list[QueueStat]:
    started_at = datetime.now(UTC)
    stats: list[QueueStat] = []

    for q in input_data.queue_names:
        depth = rabbitmq_queue_depth(
            mgmt_url=config.mgmt_url,
            queue_name=q,
            vhost=input_data.vhost,
            timeout_sec=input_data.timeout_sec,
        )
        consumers = rabbitmq_consumer_count(
            mgmt_url=config.mgmt_url,
            queue_name=q,
            vhost=input_data.vhost,
            timeout_sec=input_data.timeout_sec,
        )
        stats.append(
            QueueStat(
                queue_name=q,
                depth=depth,
                consumers=consumers,
                depth_ok=depth < config.depth_threshold,
                consumer_ok=consumers >= config.consumer_min,
            )
        )

    record_step(
        trail_steps=trail_steps,
        on_step=on_step,
        layer="oprim",
        callable_name="rabbitmq_queue_depth+rabbitmq_consumer_count",
        inputs_summary={"queues": input_data.queue_names, "vhost": input_data.vhost},
        outputs_summary={"stats_count": len(stats)},
        started_at=started_at,
    )
    return stats


def compute_fingerprint_for_diagnose_queue_health(
    config: DiagnoseQueueHealthConfig, input_data: DiagnoseQueueHealthInput
) -> str:
    return compute_fingerprint(config, input_data)
