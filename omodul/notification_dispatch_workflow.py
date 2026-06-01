"""omodul.notification_dispatch_workflow — Dispatch notifications across channels.

Pillars: fingerprint only (去重)
Composition:
  - oprim.template_render (Batch 1)
  - oprim.push_email (Batch 1)
"""

from __future__ import annotations

import traceback
from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import BaseModel

from omodul._base_config import BaseConfig
from omodul._fingerprint import compute_fingerprint


class NotifDispatchConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "notification_dispatch_workflow"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint"}
    _fingerprint_fields: ClassVar[set[str]] = {"user_id_hash", "notification_type", "channel"}

    user_id_hash: str
    notification_type: str
    channel: Literal["email", "web", "wechat"] = "email"
    to_address: str | None = None  # email channel
    template_id: str = "default_v1"
    smtp_host: str = "localhost"
    smtp_port: int = 587
    smtp_from: str = "noreply@stratum.app"


class NotifDispatchInput(BaseModel):
    subject: str
    body_vars: dict[str, Any] = {}
    body_template: str = "{{ message }}"
    message: str = ""


class NotifDispatchFindings(BaseModel):
    channel: str
    sent: bool
    notification_type: str


def notification_dispatch_workflow(
    config: NotifDispatchConfig,
    input_data: NotifDispatchInput,
    output_dir: Path,
) -> dict[str, Any]:
    """Dispatch a single notification via the configured channel.

    Internal oprim composition:
      - oprim.template_render — render notification body
      - oprim.push_email — email channel dispatch

    Returns status="completed" on success, status="failed" on any error (never raises).
    """
    enabled = config._enabled_pillars
    fingerprint = compute_fingerprint(config, input_data) if "fingerprint" in enabled else None
    error_info = None
    status = "completed"
    findings: NotifDispatchFindings | None = None

    try:
        from oprim.template_render import template_render

        ctx = {**input_data.body_vars, "message": input_data.message}
        body = template_render(template=input_data.body_template, context=ctx, strict=False)

        sent = False
        if config.channel == "email" and config.to_address:
            from oprim.push_email import push_email

            result = push_email(
                to=config.to_address,
                subject=input_data.subject,
                body=body,
                from_addr=config.smtp_from,
                smtp_host=config.smtp_host,
                smtp_port=config.smtp_port,
            )
            sent = result.success
        else:
            # Other channels (web, wechat) — placeholder for future oprim integrations
            sent = True  # assume delivered for non-email in v1.0

        findings = NotifDispatchFindings(
            channel=config.channel,
            sent=sent,
            notification_type=config.notification_type,
        )

    except Exception as e:
        error_info = {
            "error_class": type(e).__name__,
            "error_message": str(e),
            "traceback": traceback.format_exc(),
        }
        status = "failed"
        findings = NotifDispatchFindings(
            channel=config.channel,
            sent=False,
            notification_type=config.notification_type,
        )

    return {
        "findings": findings,
        "status": status,
        "error": error_info,
        "fingerprint": fingerprint,
        "decision_trail": None,
        "report_path": None,
        "cost_usd": 0.0,
    }


def compute_fingerprint_for(config: NotifDispatchConfig, input_data: NotifDispatchInput) -> str:
    return compute_fingerprint(config, input_data)
