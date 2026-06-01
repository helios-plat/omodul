"""omodul.send_welcome_email — 发送欢迎邮件轻业务事务。

Pillars: fingerprint only (去重)
Composition:
  - oprim.push_email (Batch 1)
  - oprim.template_render (Batch 1)
"""

from __future__ import annotations

import traceback
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base_config import BaseConfig
from omodul._fingerprint import compute_fingerprint


class WelcomeEmailConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "send_welcome_email"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint"}
    _fingerprint_fields: ClassVar[set[str]] = {"user_id_hash", "template_id"}

    user_id_hash: str
    template_id: str = "welcome_v1"
    to_address: str
    smtp_host: str = "localhost"
    smtp_port: int = 587
    smtp_from: str = "noreply@stratum.app"
    context: dict[str, Any] = {}


class WelcomeEmailInput(BaseModel):
    subject_override: str | None = None
    additional_context: dict[str, Any] = {}


class WelcomeEmailFindings(BaseModel):
    sent: bool
    message_id: str | None = None
    to_address: str
    template_used: str


def send_welcome_email(
    config: WelcomeEmailConfig,
    input_data: WelcomeEmailInput,
    output_dir: Path,
) -> dict[str, Any]:
    """Send a welcome email to a new user.

    Internal oprim composition:
      - oprim.template_render (Jinja2 template → email body)
      - oprim.push_email (SMTP delivery)

    Returns status="completed" on success, status="failed" on any error (never raises).
    """
    enabled = config._enabled_pillars
    fingerprint = compute_fingerprint(config, input_data) if "fingerprint" in enabled else None
    error_info = None
    status = "completed"
    findings: WelcomeEmailFindings | None = None

    try:
        # Build context
        ctx = {**config.context, **input_data.additional_context}

        # template_render: build email body
        from oprim.template_render import template_render

        templates = {
            "welcome_v1": "Hello {{ name }}! Welcome to Stratum. Your account is ready.",
        }
        template_str = templates.get(config.template_id, "Welcome to Stratum!")
        body = template_render(template=template_str, context=ctx, strict=False)

        # push_email: send
        from oprim.push_email import EmailResult, push_email

        result: EmailResult = push_email(
            to=config.to_address,
            subject=input_data.subject_override or "Welcome to Stratum!",
            body=body,
            from_addr=config.smtp_from,
            smtp_host=config.smtp_host,
            smtp_port=config.smtp_port,
            use_tls=True,
        )

        findings = WelcomeEmailFindings(
            sent=result.success,
            to_address=config.to_address,
            template_used=config.template_id,
        )

    except Exception as e:
        error_info = {
            "error_class": type(e).__name__,
            "error_message": str(e),
            "traceback": traceback.format_exc(),
        }
        status = "failed"
        findings = WelcomeEmailFindings(
            sent=False,
            to_address=config.to_address,
            template_used=config.template_id,
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


def compute_fingerprint_for(config: WelcomeEmailConfig, input_data: WelcomeEmailInput) -> str:
    return compute_fingerprint(config, input_data)
