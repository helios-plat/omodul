"""Rollback app omodul."""

from __future__ import annotations

import traceback
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, Literal

from obase.cost_tracker import CostTracker
from obase.docker import (
    docker_container_create,
    docker_container_inspect,
    docker_image_list,
    docker_image_pull,
)
from oskill import container_swap, restore_from_backup
from pydantic import BaseModel

from omodul._base_config import BaseConfig
from omodul._decision_trail import build_decision_trail, record_step
from omodul._fingerprint import compute_fingerprint
from omodul._report import write_markdown_report
from omodul._runtime import _current_cost_tracker


class RollbackAppConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "rollback_app"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {
        "app_slug",
        "instance_name",
        "rollback_to_version",
    }

    app_slug: str
    instance_name: str  # 当前运行容器名
    rollback_to_version: str  # 要回滚到的版本 tag
    restore_data: bool = True  # 是否同时恢复数据


class RollbackAppInput(BaseModel):
    docker_host: str = "unix:///var/run/docker.sock"
    caddy_admin_url: str = "http://localhost:2019"
    backup_bucket: str | None = None
    backup_key: str | None = None  # 若 restore_data=True 则必填
    target_volume: str | None = None
    aws_endpoint_url: str | None = None


class RollbackAppFindings(BaseModel):
    previous_container_id: str
    rollback_container_id: str
    data_restored: bool
    health_status: Literal["healthy", "unhealthy", "no_health_check"]
    rollback_at_utc: str


def rollback_app(
    config: RollbackAppConfig,
    input_data: RollbackAppInput,
    output_dir: Path,
    *,
    on_step: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """回滚应用: 预检 + 创建容器 + (可选) 恢复数据 + 切换 + 验证."""
    started_at = datetime.now(UTC)
    fingerprint = compute_fingerprint(config, input_data)
    cost_tracker = CostTracker(budget_usd=config.budget_usd)
    trail_steps: list[dict[str, Any]] = []
    error_info = None
    status: Literal["completed", "failed"] = "completed"
    findings = None

    token = _current_cost_tracker.set(cost_tracker)
    try:
        # 1. Preflight
        preflight_info = _stage_preflight(config, input_data, trail_steps, on_step)

        # 2. Create rollback container
        rollback_info = _stage_create_rollback_container(
            config, input_data, trail_steps, on_step
        )

        # 3. Restore data (optional)
        data_restored = False
        if config.restore_data:
            _stage_restore_data(config, input_data, trail_steps, on_step)
            data_restored = True

        # 4. Swap
        _stage_swap(
            config, input_data, preflight_info, rollback_info, trail_steps, on_step
        )

        # 5. Verify
        health_info = _stage_verify(config, input_data, trail_steps, on_step)

        findings = RollbackAppFindings(
            previous_container_id=preflight_info["previous_id"],
            rollback_container_id=rollback_info["container_id"],
            data_restored=data_restored,
            health_status=health_info["status"],
            rollback_at_utc=started_at.isoformat(),
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


def _stage_preflight(
    config: RollbackAppConfig,
    input_data: RollbackAppInput,
    trail_steps: list[dict[str, Any]],
    on_step: Callable[[dict[str, Any]], None] | None,
):
    step_start = datetime.now(UTC)

    # Inspect current
    inspect = docker_container_inspect(
        container_id=config.instance_name, docker_host=input_data.docker_host
    )
    previous_id = inspect.container_id

    # Check image
    ref = f"{config.app_slug}:{config.rollback_to_version}"
    images = docker_image_list(docker_host=input_data.docker_host)
    found = any(ref in img.get("tags", []) for img in images)
    if not found:
        docker_image_pull(
            image=config.app_slug,
            tag=config.rollback_to_version,
            docker_host=input_data.docker_host,
        )

    if config.restore_data:
        if not input_data.backup_bucket or not input_data.backup_key:
            raise ValueError("backup_bucket and backup_key are required for restore_data")
        if not input_data.target_volume:
            raise ValueError("target_volume is required for restore_data")

    record_step(
        trail_steps=trail_steps,
        on_step=on_step,
        layer="omodul",
        callable_name="_stage_preflight",
        inputs_summary={"instance": config.instance_name},
        outputs_summary={"previous_id": previous_id},
        started_at=step_start,
    )
    return {"previous_id": previous_id}


def _stage_create_rollback_container(
    config: RollbackAppConfig,
    input_data: RollbackAppInput,
    trail_steps: list[dict[str, Any]],
    on_step: Callable[[dict[str, Any]], None] | None,
):
    step_start = datetime.now(UTC)
    ts = int(step_start.timestamp())
    rb_name = f"{config.instance_name}-rb-{ts}"

    # We reuse existing labels/env if needed, but for MVP we assume config is provided.
    # Here we simplify and just create with the target image.
    res = docker_container_create(
        image=f"{config.app_slug}:{config.rollback_to_version}",
        name=rb_name,
        docker_host=input_data.docker_host,
    )

    record_step(
        trail_steps=trail_steps,
        on_step=on_step,
        layer="oprim",
        callable_name="docker_container_create",
        inputs_summary={"image": f"{config.app_slug}:{config.rollback_to_version}"},
        outputs_summary={"id": res.container_id},
        started_at=step_start,
    )
    return {"container_id": res.container_id, "name": rb_name}


def _stage_restore_data(
    config: RollbackAppConfig,
    input_data: RollbackAppInput,
    trail_steps: list[dict[str, Any]],
    on_step: Callable[[dict[str, Any]], None] | None,
):
    step_start = datetime.now(UTC)
    res = restore_from_backup(
        app_slug=config.app_slug,
        backup_bucket=input_data.backup_bucket,  # type: ignore
        backup_key=input_data.backup_key,  # type: ignore
        target_volume=input_data.target_volume,  # type: ignore
        docker_host=input_data.docker_host,
        aws_endpoint_url=input_data.aws_endpoint_url,
    )
    record_step(
        trail_steps=trail_steps,
        on_step=on_step,
        layer="oskill",
        callable_name="restore_from_backup",
        inputs_summary={"key": input_data.backup_key},
        outputs_summary={"status": "restored"},
        started_at=step_start,
    )
    return res


def _stage_swap(
    config: RollbackAppConfig,
    input_data: RollbackAppInput,
    preflight_info: dict[str, Any],
    rollback_info: dict[str, Any],
    trail_steps: list[dict[str, Any]],
    on_step: Callable[[dict[str, Any]], None] | None,
):
    step_start = datetime.now(UTC)
    res = container_swap(
        active_container_id=preflight_info["previous_id"],
        standby_container_id=rollback_info["container_id"],
        service_name=config.instance_name,
        operation="rollback",
        docker_host=input_data.docker_host,
    )
    record_step(
        trail_steps=trail_steps,
        on_step=on_step,
        layer="oskill",
        callable_name="container_swap",
        inputs_summary={"old": preflight_info["previous_id"], "new": rollback_info["container_id"]},
        outputs_summary={"status": "swapped"},
        started_at=step_start,
    )
    return res


def _stage_verify(
    config: RollbackAppConfig,
    input_data: RollbackAppInput,
    trail_steps: list[dict[str, Any]],
    on_step: Callable[[dict[str, Any]], None] | None,
):
    step_start = datetime.now(UTC)
    # Simple probe (port should be known, for MVP we assume 80 or provided)
    # In a real scenario we'd use config.health_check_url_template
    # Here we just check if it's up
    try:
        inspect = docker_container_inspect(
            container_id=config.instance_name, docker_host=input_data.docker_host
        )
        status = "healthy" if inspect.state == "running" else "unhealthy"
    except Exception:
        status = "unhealthy"

    record_step(
        trail_steps=trail_steps,
        on_step=on_step,
        layer="omodul",
        callable_name="_stage_verify",
        inputs_summary={"instance": config.instance_name},
        outputs_summary={"status": status},
        started_at=step_start,
    )
    return {"status": status}
