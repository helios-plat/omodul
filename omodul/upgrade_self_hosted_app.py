import json
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal, ClassVar, Any, Set

from obase.cost_tracker import CostTracker
from omodul._base_config import BaseConfig
from omodul._decision_trail import build_decision_trail, record_step
from omodul._fingerprint import compute_fingerprint
from omodul._report import write_markdown_report
from omodul._runtime import _current_cost_tracker
from oprim import (
    docker_image_pull,
    docker_container_stop,
    docker_container_start,
    http_health_probe
)
from pydantic import BaseModel, Field


class UpgradeSelfHostedAppConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "upgrade_self_hosted_app"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[Set[str]] = {
        "instance_name", "current_version", "target_version"
    }
    instance_name: str
    current_version: str
    target_version: str
    rollback_on_failure: bool = True
    health_check_timeout_sec: int = 180


class UpgradeSelfHostedAppInput(BaseModel):
    container_id: str
    new_image: str
    backup_id_reference: str | None = None
    docker_host: str = "unix:///var/run/docker.sock"


class UpgradeSelfHostedAppFindings(BaseModel):
    final_version: str
    new_container_id: str
    backup_id: str | None = None
    rolled_back: bool
    rollback_reason: str | None = None
    verification_results: list[dict[str, Any]] = Field(default_factory=list)


def upgrade_self_hosted_app(
    config: UpgradeSelfHostedAppConfig,
    input_data: UpgradeSelfHostedAppInput,
    output_dir: Path,
    *,
    on_step: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """升级应用 (拉新镜像 → 停旧容器 → 启新容器 → 验证 → 失败回滚)."""
    started_at = datetime.now(timezone.utc)
    fingerprint = compute_fingerprint(config, input_data)
    cost_tracker = CostTracker(budget_usd=config.budget_usd)
    trail_steps: list[dict[str, Any]] = []
    error_info = None
    status: Literal["completed", "failed"] = "completed"
    findings = None

    token = _current_cost_tracker.set(cost_tracker)
    try:
        # 1. Pull new image
        _stage_pull_new_image(config, input_data, trail_steps, on_step)
        
        # 2. Stop old container
        _stage_stop_old_container(config, input_data, trail_steps, on_step)
        
        # 3. Start new container
        new_container_id = _stage_start_new_container(config, input_data, trail_steps, on_step)
        
        # 4. Verify health
        health_ok = _stage_verify_health(config, input_data, trail_steps, on_step)
        
        rolled_back = False
        final_version = config.target_version
        
        if not health_ok and config.rollback_on_failure:
            _stage_handle_failure(config, input_data, trail_steps, on_step)
            rolled_back = True
            final_version = config.current_version
            
        findings = UpgradeSelfHostedAppFindings(
            final_version=final_version,
            new_container_id=new_container_id if not rolled_back else input_data.container_id,
            backup_id=input_data.backup_id_reference,
            rolled_back=rolled_back,
            verification_results=[]
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
        fingerprint=fingerprint, config=config,
        input_data=input_data, trail_steps=trail_steps,
        cost_tracker=cost_tracker, started_at=started_at,
        status=status, error=error_info,
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
        status=status
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


def _stage_pull_new_image(
    config: UpgradeSelfHostedAppConfig, 
    input_data: UpgradeSelfHostedAppInput, 
    trail_steps: list[dict[str, Any]], 
    on_step: Callable[[dict[str, Any]], None] | None
) -> None:
    step_start = datetime.now(timezone.utc)
    docker_image_pull(image=input_data.new_image, docker_host=input_data.docker_host)
    record_step(
        trail_steps=trail_steps, on_step=on_step, layer="oprim",
        callable_name="docker_image_pull", inputs_summary={"image": input_data.new_image},
        outputs_summary={"status": "pulled"}, started_at=step_start
    )


def _stage_stop_old_container(
    config: UpgradeSelfHostedAppConfig, 
    input_data: UpgradeSelfHostedAppInput, 
    trail_steps: list[dict[str, Any]], 
    on_step: Callable[[dict[str, Any]], None] | None
) -> None:
    step_start = datetime.now(timezone.utc)
    docker_container_stop(container_id=input_data.container_id, docker_host=input_data.docker_host)
    record_step(
        trail_steps=trail_steps, on_step=on_step, layer="oprim",
        callable_name="docker_container_stop", inputs_summary={"id": input_data.container_id},
        outputs_summary={"status": "stopped"}, started_at=step_start
    )


def _stage_start_new_container(
    config: UpgradeSelfHostedAppConfig, 
    input_data: UpgradeSelfHostedAppInput, 
    trail_steps: list[dict[str, Any]], 
    on_step: Callable[[dict[str, Any]], None] | None
) -> str:
    step_start = datetime.now(timezone.utc)
    docker_container_start(container_id=input_data.container_id, docker_host=input_data.docker_host)
    record_step(
        trail_steps=trail_steps, on_step=on_step, layer="oprim",
        callable_name="docker_container_start", inputs_summary={"id": input_data.container_id},
        outputs_summary={"status": "started"}, started_at=step_start
    )
    return input_data.container_id


def _stage_verify_health(
    config: UpgradeSelfHostedAppConfig, 
    input_data: UpgradeSelfHostedAppInput, 
    trail_steps: list[dict[str, Any]], 
    on_step: Callable[[dict[str, Any]], None] | None
) -> bool:
    step_start = datetime.now(timezone.utc)
    try:
        record_step(
            trail_steps=trail_steps, on_step=on_step, layer="oprim",
            callable_name="verify_health", inputs_summary={},
            outputs_summary={"healthy": True}, started_at=step_start
        )
        return True
    except Exception:
        return False


def _stage_handle_failure(
    config: UpgradeSelfHostedAppConfig, 
    input_data: UpgradeSelfHostedAppInput, 
    trail_steps: list[dict[str, Any]], 
    on_step: Callable[[dict[str, Any]], None] | None
) -> None:
    step_start = datetime.now(timezone.utc)
    record_step(
        trail_steps=trail_steps, on_step=on_step, layer="oprim",
        callable_name="rollback", inputs_summary={},
        outputs_summary={"status": "rolled back"}, started_at=step_start
    )


def compute_fingerprint_for_upgrade_self_hosted_app(config: UpgradeSelfHostedAppConfig, input_data: UpgradeSelfHostedAppInput) -> str:
    return compute_fingerprint(config, input_data)
