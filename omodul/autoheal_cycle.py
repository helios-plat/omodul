"""Autoheal cycle omodul."""

from __future__ import annotations

import time
import traceback
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, Literal

from obase.cost_tracker import CostTracker
from oprim import db_query, db_update, docker_container_restart, http_post_webhook
from oskill import verify_health_after_action
from pydantic import BaseModel

from omodul._base_config import BaseConfig
from omodul._decision_trail import build_decision_trail, record_step
from omodul._fingerprint import compute_fingerprint
from omodul._report import write_markdown_report
from omodul._runtime import _current_cost_tracker


class AutohealAction(BaseModel):
    source: str
    action_taken: Literal["restart", "skipped", "failed"]
    container_id: str | None
    reason: str
    recovered: bool


class AutohealCycleConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "autoheal_cycle"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {"cycle_id"}

    cycle_id: str
    max_alerts_per_run: int = 10
    restart_timeout_sec: int = 10
    verify_timeout_sec: int = 30
    skip_sources: list[str] = []  # 不自动处理的 source 白名单


class AutohealCycleInput(BaseModel):
    docker_host: str = "unix:///var/run/docker.sock"
    webhook_url: str | None = None  # 处理结果通知


class AutohealCycleFindings(BaseModel):
    cycle_id: str
    alerts_processed: int
    actions: list[AutohealAction]
    recovered_count: int
    failed_count: int
    skipped_count: int
    duration_ms: int


def autoheal_cycle(
    config: AutohealCycleConfig,
    input_data: AutohealCycleInput,
    output_dir: Path,
    *,
    on_step: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """自愈周期: 获取告警 + 重启容器 + 验证 + 标记处理."""
    started_at = datetime.now(UTC)
    fingerprint = compute_fingerprint(config, input_data)
    cost_tracker = CostTracker(budget_usd=config.budget_usd)
    trail_steps: list[dict[str, Any]] = []
    error_info = None
    status: Literal["completed", "failed"] = "completed"
    findings = None

    token = _current_cost_tracker.set(cost_tracker)
    try:
        # 1. Fetch alerts
        alerts = _stage_fetch_alerts(config, input_data, trail_steps, on_step)

        # 2. Attempt restart
        actions = _stage_attempt_restart(config, input_data, alerts, trail_steps, on_step)

        # 3. Verify recovery
        actions = _stage_verify_recovery(config, input_data, actions, trail_steps, on_step)

        # 4. Mark handled
        _stage_mark_handled(config, input_data, alerts, trail_steps, on_step)

        recovered = sum(1 for a in actions if a.recovered)
        failed = sum(1 for a in actions if a.action_taken == "failed")
        skipped = sum(1 for a in actions if a.action_taken == "skipped")

        findings = AutohealCycleFindings(
            cycle_id=config.cycle_id,
            alerts_processed=len(alerts),
            actions=actions,
            recovered_count=recovered,
            failed_count=failed,
            skipped_count=skipped,
            duration_ms=int((datetime.now(UTC) - started_at).total_seconds() * 1000),
        )

    except Exception as e:
        status = "failed"
        error_info = {
            "type": e.__class__.__name__,
            "message": str(e),
            "traceback": traceback.format_exc(),
        }
    finally:
        _current_cost_tracker.reset(token)

    result = {
        "status": status,
        "fingerprint": fingerprint,
        "findings": findings.model_dump() if findings else None,
        "error": error_info,
        "decision_trail": build_decision_trail(
            omodul_name=config._omodul_name,
            omodul_version=config._omodul_version,
            status=status,
            started_at=started_at,
            steps=trail_steps,
        ),
    }

    write_markdown_report(output_dir / "report.md", result)
    return result


def _stage_fetch_alerts(
    config: AutohealCycleConfig,
    input_data: AutohealCycleInput,
    trail_steps: list[dict[str, Any]],
    on_step: Callable[[dict[str, Any]], None] | None,
):
    step_start = datetime.now(UTC)
    query = (
        "SELECT id, source FROM aegis_alert_events "
        "WHERE handled=false AND severity='critical' "
        f"ORDER BY created_at ASC LIMIT {config.max_alerts_per_run}"
    )
    alerts = db_query(query)

    record_step(
        trail_steps=trail_steps,
        on_step=on_step,
        layer="oprim",
        callable_name="db_query",
        inputs_summary={"limit": config.max_alerts_per_run},
        outputs_summary={"alerts_found": len(alerts)},
        started_at=step_start,
    )
    return alerts


def _stage_attempt_restart(
    config: AutohealCycleConfig,
    input_data: AutohealCycleInput,
    alerts: list[dict[str, Any]],
    trail_steps: list[dict[str, Any]],
    on_step: Callable[[dict[str, Any]], None] | None,
):
    step_start = datetime.now(UTC)
    actions: list[AutohealAction] = []

    for alert in alerts:
        source = alert.get("source", "")
        if not source.startswith("container:"):
            actions.append(
                AutohealAction(
                    source=source,
                    action_taken="skipped",
                    container_id=None,
                    reason="node-level alert requires manual intervention",
                    recovered=False,
                )
            )
            continue

        container_name = source.replace("container:", "")
        if container_name in config.skip_sources:
            actions.append(
                AutohealAction(
                    source=source,
                    action_taken="skipped",
                    container_id=container_name,
                    reason="source is in skip list",
                    recovered=False,
                )
            )
            continue

        try:
            docker_container_restart(
                container_id=container_name,
                timeout_sec=config.restart_timeout_sec,
                docker_host=input_data.docker_host,
            )
            actions.append(
                AutohealAction(
                    source=source,
                    action_taken="restart",
                    container_id=container_name,
                    reason="auto-restart triggered",
                    recovered=False,  # pending verification
                )
            )
        except Exception as exc:
            actions.append(
                AutohealAction(
                    source=source,
                    action_taken="failed",
                    container_id=container_name,
                    reason=f"restart failed: {exc}",
                    recovered=False,
                )
            )

    record_step(
        trail_steps=trail_steps,
        on_step=on_step,
        layer="oprim",
        callable_name="docker_container_restart",
        inputs_summary={"alerts": len(alerts)},
        outputs_summary={"restarts": sum(1 for a in actions if a.action_taken == "restart")},
        started_at=step_start,
    )
    return actions


def _stage_verify_recovery(
    config: AutohealCycleConfig,
    input_data: AutohealCycleInput,
    actions: list[AutohealAction],
    trail_steps: list[dict[str, Any]],
    on_step: Callable[[dict[str, Any]], None] | None,
):
    step_start = datetime.now(UTC)
    # Wait a bit for containers to settle
    time.sleep(5)

    for action in actions:
        if action.action_taken != "restart" or not action.container_id:
            continue

        try:
            res = verify_health_after_action(
                container_id=action.container_id,
                docker_host=input_data.docker_host,
                timeout_sec=config.verify_timeout_sec,
            )
            action.recovered = res.healthy
            if not res.healthy:
                action.reason += f" | verify failed: {res.detail}"
                # Emit secondary alert if failed to recover
                if input_data.webhook_url:
                    http_post_webhook(
                        url=input_data.webhook_url,
                        payload={
                            "severity": "critical",
                            "source": f"autoheal:{action.container_id}",
                            "reason": f"Auto-restart failed to recover service: {res.detail}",
                            "at": datetime.now(UTC).isoformat(),
                        },
                    )
        except Exception as exc:
            action.recovered = False
            action.reason += f" | verify error: {exc}"

    record_step(
        trail_steps=trail_steps,
        on_step=on_step,
        layer="oskill",
        callable_name="verify_health_after_action",
        inputs_summary={"verify_count": sum(1 for a in actions if a.action_taken == "restart")},
        outputs_summary={"recovered": sum(1 for a in actions if a.recovered)},
        started_at=step_start,
    )
    return actions


def _stage_mark_handled(
    config: AutohealCycleConfig,
    input_data: AutohealCycleInput,
    alerts: list[dict[str, Any]],
    trail_steps: list[dict[str, Any]],
    on_step: Callable[[dict[str, Any]], None] | None,
):
    step_start = datetime.now(UTC)
    now_str = datetime.now(UTC).isoformat()
    for alert in alerts:
        alert_id = alert.get("id")
        if alert_id:
            db_update(
                "UPDATE aegis_alert_events "
                f"SET handled=true, handled_at='{now_str}' "
                f"WHERE id={alert_id}"
            )

    record_step(
        trail_steps=trail_steps,
        on_step=on_step,
        layer="oprim",
        callable_name="db_update",
        inputs_summary={"alerts_marked": len(alerts)},
        outputs_summary={"status": "updated"},
        started_at=step_start,
    )
