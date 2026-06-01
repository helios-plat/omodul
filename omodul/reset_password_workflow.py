"""omodul.reset_password_workflow — 密码重置工作流。

Pillars: decision_trail (安全敏感，只审计)
Composition:
  - oprim.crypto_token_generate (Batch 1)
  - oprim.db_write (Batch 1) — 存储 reset token hash
  - oprim.push_email (Batch 1)
  - oprim.template_render (Batch 1)
"""

from __future__ import annotations

import hashlib
import json
import traceback
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

from obase.cost_tracker import CostTracker
from pydantic import BaseModel

from omodul._base_config import BaseConfig
from omodul._decision_trail import build_decision_trail, record_step


class ResetPasswordConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "reset_password_workflow"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail"}
    _fingerprint_fields: ClassVar[set[str]] = set()

    user_id: str
    user_id_hash: str
    to_address: str
    db_dsn: str
    reset_tokens_table: str = "password_reset_tokens"
    token_expires_minutes: int = 60
    smtp_host: str = "localhost"
    smtp_port: int = 587
    smtp_from: str = "noreply@stratum.app"
    base_url: str = "https://stratum.app"


class ResetPasswordInput(BaseModel):
    request_ip: str | None = None


class ResetPasswordFindings(BaseModel):
    token_id: str
    reset_url: str
    token_expires_at: datetime


def reset_password_workflow(
    config: ResetPasswordConfig,
    input_data: ResetPasswordInput,
    output_dir: Path,
) -> dict[str, Any]:
    """Generate a password reset token and email it to the user.

    Internal oprim composition:
      - oprim.crypto_token_generate — secure random token
      - oprim.db_write — upsert token record
      - oprim.template_render — email template
      - oprim.push_email — SMTP delivery

    Security: decision_trail only (no fingerprint, audit log mandatory).
    """
    started_at = datetime.now(UTC)
    enabled = config._enabled_pillars
    trail_steps: list[dict[str, Any]] = []
    cost_tracker = CostTracker()
    error_info = None
    status = "completed"
    findings: ResetPasswordFindings | None = None
    dt: dict[str, Any] = {}

    try:
        # Stage 1: Generate token
        from oprim.crypto_token_generate import crypto_token_generate

        token = crypto_token_generate(length=32, url_safe=True)
        expires_at = datetime.now(UTC) + timedelta(minutes=config.token_expires_minutes)

        step_start = datetime.now(UTC)
        record_step(
            trail_steps=trail_steps,
            on_step=None,
            layer="oprim",
            callable_name="crypto_token_generate",
            inputs_summary={"length": 32},
            outputs_summary={"token_length": len(token)},
            started_at=step_start,
        )

        # Stage 2: Store token hash in DB
        from oprim.db_write import db_write

        token_hash = hashlib.sha256(token.encode()).hexdigest()
        step_start = datetime.now(UTC)
        db_write(
            dsn=config.db_dsn,
            table=config.reset_tokens_table,
            data={
                "user_id": config.user_id,
                "token_hash": token_hash,
                "expires_at": expires_at.isoformat(),
                "request_ip": input_data.request_ip,
            },
            conflict_on=["user_id"],
        )
        record_step(
            trail_steps=trail_steps,
            on_step=None,
            layer="oprim",
            callable_name="db_write",
            inputs_summary={
                "table": config.reset_tokens_table,
                "user_id_hash": config.user_id_hash,
            },
            outputs_summary={"stored": True},
            started_at=step_start,
        )

        # Stage 3: Build email + send
        from oprim.push_email import push_email
        from oprim.template_render import template_render

        reset_url = f"{config.base_url}/reset-password?token={token}"
        body = template_render(
            template="Click to reset: {{ reset_url }} (expires {{ expires_at }})",
            context={
                "reset_url": reset_url,
                "expires_at": expires_at.strftime("%Y-%m-%d %H:%M UTC"),
            },
            strict=False,
        )
        push_email(
            to=config.to_address,
            subject="Reset your Stratum password",
            body=body,
            from_addr=config.smtp_from,
            smtp_host=config.smtp_host,
            smtp_port=config.smtp_port,
        )

        findings = ResetPasswordFindings(
            token_id=token_hash[:12],
            reset_url=reset_url,
            token_expires_at=expires_at,
        )

    except Exception as e:
        error_info = {
            "error_class": type(e).__name__,
            "error_message": str(e),
            "traceback": traceback.format_exc(),
        }
        status = "failed"

    finally:
        # decision_trail always written (security requirement)
        if "decision_trail" in enabled:
            dt = build_decision_trail(
                fingerprint="",
                config=config,
                input_data=input_data,
                trail_steps=trail_steps,
                started_at=started_at,
                status=status,
                error=error_info,
                cost_tracker=cost_tracker,
            )
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "decision_trail.json").write_text(
                json.dumps(dt, indent=2, ensure_ascii=False, default=str)
            )

    return {
        "findings": findings,
        "status": status,
        "error": error_info,
        "fingerprint": None,
        "decision_trail": dt if "decision_trail" in enabled else None,
        "report_path": None,
        "cost_usd": 0.0,
    }
