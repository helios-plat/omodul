import json
import traceback
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, Literal

from obase.cost_tracker import CostTracker
from pydantic import BaseModel

from oprim import postgres_long_running_queries, postgres_locks_status
from oskill import diagnose_pattern_match, compute_severity_score, classify_signal

from omodul._base_config import BaseConfig
from omodul._decision_trail import build_decision_trail, record_step
from omodul._fingerprint import compute_fingerprint
from omodul._report import write_markdown_report
from omodul._runtime import _current_cost_tracker


class DiagnoseConnectionPoolConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "diagnose_connection_pool"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {"dsn_hash", "slow_threshold_ms"}
    dsn: str
    slow_threshold_ms: int = 5000
    dsn_hash: str = ""


class DiagnoseConnectionPoolInput(BaseModel):
    active_connections: int = 0
    max_connections: int = 100
    timeout_sec: int = 10


class SlowQuerySummary(BaseModel):
    pid: int
    duration_ms: float
    query_snippet: str
    state: str


class DiagnoseConnectionPoolFindings(BaseModel):
    slow_queries: list[SlowQuerySummary]
    lock_count: int
    active_connections: int
    max_connections: int
    connection_used_percent: float
    signal_class: str
    pattern_name: str | None
    severity_score: float
    severity_label: str
    needs_deep_investigation: bool
    recommendation: str


def diagnose_connection_pool(
    config: DiagnoseConnectionPoolConfig,
    input_data: DiagnoseConnectionPoolInput,
    output_dir: Path,
    *,
    on_step: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """PostgreSQL 连接池诊断 — slow queries + lock count + oskill signal analysis.

    B option: returns needs_deep_investigation flag instead of calling oservice.
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
        # Stage 1: gather postgres metrics
        slow_queries, lock_count = _stage_gather_pg_metrics(
            config, input_data, trail_steps, on_step
        )

        # Stage 2: build aggregate signal
        conn_pct = input_data.active_connections / max(input_data.max_connections, 1) * 100
        slow_count = len(slow_queries)
        signal = {
            "message": f"{slow_count} slow queries, {lock_count} locks, {conn_pct:.0f}% connections",
            "resource_used_percent": conn_pct,
            "error_rate": slow_count / max(input_data.active_connections, 1),
            "latency_p99_ms": max((q.duration_ms for q in slow_queries), default=0.0),
            "active_connections": input_data.active_connections,
        }

        # Stage 3: oskill classification + pattern + severity
        classification = classify_signal(signal=signal)
        pattern_result = diagnose_pattern_match(signal=signal)
        severity_result = compute_severity_score(signal=signal)

        record_step(
            trail_steps=trail_steps,
            on_step=on_step,
            layer="oskill",
            callable_name="classify_signal+diagnose_pattern_match+compute_severity_score",
            inputs_summary={"slow_count": slow_count, "lock_count": lock_count},
            outputs_summary={
                "signal_class": classification.signal_class,
                "pattern": pattern_result.pattern_name,
                "severity": severity_result.label,
            },
            started_at=started_at,
        )

        needs_deep = (
            severity_result.label in ("critical", "high") or slow_count >= 5 or lock_count >= 10
        )
        recommendation = (
            f"{slow_count} slow queries (>{config.slow_threshold_ms}ms), "
            f"{lock_count} locks detected. Severity: {severity_result.label}. "
            + ("Escalate to agentic investigation." if needs_deep else "Within acceptable range.")
        )

        findings = DiagnoseConnectionPoolFindings(
            slow_queries=slow_queries,
            lock_count=lock_count,
            active_connections=input_data.active_connections,
            max_connections=input_data.max_connections,
            connection_used_percent=round(conn_pct, 2),
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


def _stage_gather_pg_metrics(
    config: DiagnoseConnectionPoolConfig,
    input_data: DiagnoseConnectionPoolInput,
    trail_steps: list[dict[str, Any]],
    on_step: Callable[[dict[str, Any]], None] | None,
) -> tuple[list[SlowQuerySummary], int]:
    started_at = datetime.now(UTC)

    raw_slow = postgres_long_running_queries(
        dsn=config.dsn,
        threshold_ms=config.slow_threshold_ms,
        timeout_sec=input_data.timeout_sec,
    )
    slow_summaries: list[SlowQuerySummary] = []
    for row in raw_slow:
        slow_summaries.append(
            SlowQuerySummary(
                pid=int(row.get("pid", 0)),
                duration_ms=float(row.get("duration_ms", row.get("duration", 0))),
                query_snippet=str(row.get("query", ""))[:120],
                state=str(row.get("state", "")),
            )
        )

    raw_locks = postgres_locks_status(
        dsn=config.dsn,
        timeout_sec=input_data.timeout_sec,
    )
    lock_count = len(raw_locks) if isinstance(raw_locks, list) else 0

    record_step(
        trail_steps=trail_steps,
        on_step=on_step,
        layer="oprim",
        callable_name="postgres_long_running_queries+postgres_locks_status",
        inputs_summary={"threshold_ms": config.slow_threshold_ms},
        outputs_summary={"slow_queries": len(slow_summaries), "lock_count": lock_count},
        started_at=started_at,
    )
    return slow_summaries, lock_count


def compute_fingerprint_for_diagnose_connection_pool(
    config: DiagnoseConnectionPoolConfig, input_data: DiagnoseConnectionPoolInput
) -> str:
    return compute_fingerprint(config, input_data)
