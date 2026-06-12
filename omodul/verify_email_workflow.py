"""omodul.verify_email_workflow — 邮件验证工作流（两阶段）。

Pillars: fingerprint
Composition:
  - oprim.otp_generate (Batch 1)
  - obase.persistence.update_one (Batch 1)
  - oprim.push_email (Batch 1)
  - oprim.template_render (Batch 1)
"""

from __future__ import annotations

import traceback
from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import BaseModel

from obase.persistence import PgPool, update_one
from omodul._base_config import BaseConfig
from omodul._fingerprint import compute_fingerprint


class VerifyEmailConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "verify_email_workflow"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint"}
    _fingerprint_fields: ClassVar[set[str]] = {"user_id_hash", "action"}

    user_id_hash: str
    user_id: str
    to_address: str
    action: Literal["send", "verify"] = "send"
    db_dsn: str
    users_table: str = "users"
    smtp_host: str = "localhost"
    smtp_port: int = 587
    smtp_from: str = "noreply@stratum.app"


class VerifyEmailInput(BaseModel):
    otp_secret: str | None = None  # Required for action="verify"
    otp_code: str | None = None  # Required for action="verify"


class VerifyEmailFindings(BaseModel):
    action: str
    sent: bool = False
    verified: bool = False
    otp_secret: str | None = None  # Returned on send (caller stores it)


async def verify_email_workflow(
    config: VerifyEmailConfig,
    input_data: VerifyEmailInput,
    output_dir: Path,
    *,
    on_step: Any = None,
) -> dict[str, Any]:
    """Two-phase email verification: send OTP or verify OTP.

    Internal composition:
      - oprim.otp_generate — TOTP generation (send phase)
      - oprim.template_render — email body (send phase)
      - oprim.push_email — SMTP delivery (send phase)
      - obase.persistence.update_one — mark email_verified=True (verify phase)
    """
    enabled = config._enabled_pillars
    fingerprint = compute_fingerprint(config, input_data) if "fingerprint" in enabled else None
    error_info = None
    status = "completed"
    findings: VerifyEmailFindings | None = None

    try:
        if config.action == "send":
            findings = _stage_send(config, input_data)
        else:
            findings = await _stage_verify(config, input_data)

    except Exception as e:
        error_info = {
            "error_class": type(e).__name__,
            "error_message": str(e),
            "traceback": traceback.format_exc(),
        }
        status = "failed"
        findings = VerifyEmailFindings(action=config.action)

    return {
        "findings": findings,
        "status": status,
        "error": error_info,
        "fingerprint": fingerprint,
        "decision_trail": None,
        "report_path": None,
        "cost_usd": 0.0,
    }


def _stage_send(config: VerifyEmailConfig, input_data: VerifyEmailInput) -> VerifyEmailFindings:
    from oprim.otp_generate import otp_generate
    from oprim.push_email import push_email
    from oprim.template_render import template_render

    otp_result = otp_generate(digits=6, period=300)  # 5-min period
    body = template_render(
        template="Your Stratum verification code is: {{ code }} (valid 5 minutes)",
        context={"code": otp_result.code},
        strict=False,
    )
    push_email(
        to=config.to_address,
        subject="Verify your Stratum email",
        body=body,
        from_addr=config.smtp_from,
        smtp_host=config.smtp_host,
        smtp_port=config.smtp_port,
    )
    return VerifyEmailFindings(action="send", sent=True, otp_secret=otp_result.secret)


async def _stage_verify(
    config: VerifyEmailConfig, input_data: VerifyEmailInput
) -> VerifyEmailFindings:
    from oprim.otp_generate import otp_verify

    if not input_data.otp_secret or not input_data.otp_code:
        raise ValueError("otp_secret and otp_code required for action='verify'")

    is_valid = otp_verify(secret=input_data.otp_secret, code=input_data.otp_code)
    if is_valid:
        pool = await PgPool.get_or_create(dsn=config.db_dsn)
        await update_one(
            pool=pool,
            table=config.users_table,
            id=config.user_id,
            data={"email_verified": True},
        )
    return VerifyEmailFindings(action="verify", verified=is_valid)


def compute_fingerprint_for(config: VerifyEmailConfig, input_data: VerifyEmailInput) -> str:
    return compute_fingerprint(config, input_data)
