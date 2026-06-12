"""Tests for omodul-024/025/026: send_welcome_email, reset_password_workflow, verify_email_workflow."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Stub missing deps (jinja2, alembic, sqlalchemy, frontmatter) not installed in omodul venv.
# These are pulled in transitively when omodul/__init__.py imports oprim.*
_STUB_MODULES = [
    "jinja2",
    "alembic",
    "alembic.command",
    "alembic.config",
    "alembic.runtime",
    "alembic.runtime.migration",
    "alembic.script",
    "sqlalchemy",
    "frontmatter",
]
for _mod in _STUB_MODULES:
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

# Import directly from submodule to avoid omodul.__init__ chain import issues
from omodul.send_welcome_email import (  # noqa: E402
    WelcomeEmailConfig,
    WelcomeEmailInput,
    compute_fingerprint_for,
    send_welcome_email,
)
from omodul.reset_password_workflow import (  # noqa: E402
    ResetPasswordConfig,
    ResetPasswordInput,
    reset_password_workflow,
)
from omodul.verify_email_workflow import (  # noqa: E402
    VerifyEmailConfig,
    VerifyEmailInput,
    verify_email_workflow,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _email_result(success: bool = True):
    from oprim.push_email import EmailResult

    return EmailResult(success=success, to="user@example.com", subject="test")


def _otp_result():
    from oprim.otp_generate import OTPResult

    return OTPResult(
        secret="JBSWY3DPEHPK3PXP",
        code="123456",
        expires_at=datetime(2099, 1, 1, tzinfo=UTC),
    )


def _welcome_config(user_id_hash: str = "hash-abc", **kw) -> WelcomeEmailConfig:
    return WelcomeEmailConfig(
        user_id_hash=user_id_hash,
        to_address="user@example.com",
        **kw,
    )


def _reset_config(**kw) -> ResetPasswordConfig:
    return ResetPasswordConfig(
        user_id="user-1",
        user_id_hash="hash-abc",
        to_address="user@example.com",
        db_dsn="postgresql://localhost/test",
        **kw,
    )


def _verify_config(action: str = "send", **kw) -> VerifyEmailConfig:
    return VerifyEmailConfig(
        user_id_hash="hash-abc",
        user_id="user-1",
        to_address="user@example.com",
        action=action,
        db_dsn="postgresql://localhost/test",
        **kw,
    )


# ---------------------------------------------------------------------------
# omodul-024: send_welcome_email
# ---------------------------------------------------------------------------


class TestSendWelcomeEmail:
    def _patch_email(self, success: bool = True):
        """Patch template_render (returns str) and push_email (returns EmailResult)."""
        return [
            patch(
                "oprim.template_render.template_render",
                return_value="Hello Alice! Welcome to Stratum.",
            ),
            patch("oprim.push_email.push_email", return_value=_email_result(success)),
        ]

    def test_success_status_completed(self, tmp_path):
        with (
            patch(
                "oprim.template_render.template_render",
                return_value="Welcome body",
            ),
            patch("oprim.push_email.push_email", return_value=_email_result()),
        ):
            result = send_welcome_email(
                _welcome_config(),
                WelcomeEmailInput(additional_context={"name": "Alice"}),
                tmp_path,
            )
        assert result["status"] == "completed"
        assert result["findings"].sent is True
        assert result["findings"].to_address == "user@example.com"
        assert result["findings"].template_used == "welcome_v1"

    def test_fingerprint_not_none(self, tmp_path):
        with (
            patch("oprim.template_render.template_render", return_value="body"),
            patch("oprim.push_email.push_email", return_value=_email_result()),
        ):
            result = send_welcome_email(_welcome_config(), WelcomeEmailInput(), tmp_path)
        assert result["fingerprint"] is not None
        assert len(result["fingerprint"]) == 64

    def test_failed_smtp_status_failed_no_raise(self, tmp_path):
        with (
            patch("oprim.template_render.template_render", return_value="body"),
            patch("oprim.push_email.push_email", side_effect=OSError("connection refused")),
        ):
            result = send_welcome_email(_welcome_config(), WelcomeEmailInput(), tmp_path)
        assert result["status"] == "failed"
        assert result["error"] is not None
        assert result["findings"].sent is False

    def test_decision_trail_is_none(self, tmp_path):
        with (
            patch("oprim.template_render.template_render", return_value="body"),
            patch("oprim.push_email.push_email", return_value=_email_result()),
        ):
            result = send_welcome_email(_welcome_config(), WelcomeEmailInput(), tmp_path)
        assert result["decision_trail"] is None

    def test_compute_fingerprint_for_deterministic(self):
        config = _welcome_config()
        inp = WelcomeEmailInput()
        fp1 = compute_fingerprint_for(config, inp)
        fp2 = compute_fingerprint_for(config, inp)
        assert fp1 == fp2
        assert len(fp1) == 64

    def test_subject_override_accepted(self, tmp_path):
        with (
            patch("oprim.template_render.template_render", return_value="body"),
            patch("oprim.push_email.push_email", return_value=_email_result()),
        ):
            result = send_welcome_email(
                _welcome_config(),
                WelcomeEmailInput(subject_override="Custom Subject"),
                tmp_path,
            )
        assert result["status"] == "completed"

    def test_cost_is_zero(self, tmp_path):
        with (
            patch("oprim.template_render.template_render", return_value="body"),
            patch("oprim.push_email.push_email", return_value=_email_result()),
        ):
            result = send_welcome_email(_welcome_config(), WelcomeEmailInput(), tmp_path)
        assert result["cost_usd"] == 0.0

    def test_fingerprint_differs_for_different_user(self, tmp_path):
        inp = WelcomeEmailInput()
        with (
            patch("oprim.template_render.template_render", return_value="body"),
            patch("oprim.push_email.push_email", return_value=_email_result()),
        ):
            r1 = send_welcome_email(_welcome_config(user_id_hash="aaa"), inp, tmp_path)
            r2 = send_welcome_email(_welcome_config(user_id_hash="bbb"), inp, tmp_path)
        assert r1["fingerprint"] != r2["fingerprint"]


# ---------------------------------------------------------------------------
# omodul-025: reset_password_workflow
# ---------------------------------------------------------------------------


class TestResetPasswordWorkflow:
    async def test_success_status_completed(self, tmp_path):
        with (
            patch("oprim.template_render.template_render", return_value="body"),
            patch("oprim.push_email.push_email", return_value=_email_result()),
            patch("obase.persistence.pool.PgPool.get_or_create", new_callable=AsyncMock, return_value=MagicMock()),
            patch("omodul.reset_password_workflow.write_one", new_callable=AsyncMock, return_value=1),
        ):
            result = await reset_password_workflow(
                _reset_config(), ResetPasswordInput(request_ip="1.2.3.4"), tmp_path
            )
        assert result["status"] == "completed"
        assert result["findings"] is not None

    async def test_findings_has_reset_url_and_expires(self, tmp_path):
        with (
            patch("oprim.template_render.template_render", return_value="body"),
            patch("oprim.push_email.push_email", return_value=_email_result()),
            patch("obase.persistence.pool.PgPool.get_or_create", new_callable=AsyncMock, return_value=MagicMock()),
            patch("omodul.reset_password_workflow.write_one", new_callable=AsyncMock, return_value=1),
        ):
            result = await reset_password_workflow(_reset_config(), ResetPasswordInput(), tmp_path)
        findings = result["findings"]
        assert "reset-password?token=" in findings.reset_url
        assert findings.token_expires_at is not None
        assert len(findings.token_id) == 12

    async def test_decision_trail_json_written(self, tmp_path):
        with (
            patch("oprim.template_render.template_render", return_value="body"),
            patch("oprim.push_email.push_email", return_value=_email_result()),
            patch("obase.persistence.pool.PgPool.get_or_create", new_callable=AsyncMock, return_value=MagicMock()),
            patch("omodul.reset_password_workflow.write_one", new_callable=AsyncMock, return_value=1),
        ):
            await reset_password_workflow(_reset_config(), ResetPasswordInput(), tmp_path)
        trail_file = tmp_path / "decision_trail.json"
        assert trail_file.exists()
        data = json.loads(trail_file.read_text())
        assert data["omodul_name"] == "reset_password_workflow"
        assert data["status"] == "completed"

    async def test_db_write_failure_status_failed(self, tmp_path):
        with (
            patch("obase.persistence.pool.PgPool.get_or_create", new_callable=AsyncMock, return_value=MagicMock()),
            patch("omodul.reset_password_workflow.write_one", new_callable=AsyncMock, side_effect=Exception("DB down")),
        ):
            result = await reset_password_workflow(_reset_config(), ResetPasswordInput(), tmp_path)
        assert result["status"] == "failed"
        assert result["error"]["error_message"] == "DB down"

    async def test_decision_trail_still_written_on_failure(self, tmp_path):
        with (
            patch("obase.persistence.pool.PgPool.get_or_create", new_callable=AsyncMock, return_value=MagicMock()),
            patch("omodul.reset_password_workflow.write_one", new_callable=AsyncMock, side_effect=Exception("DB down")),
        ):
            await reset_password_workflow(_reset_config(), ResetPasswordInput(), tmp_path)
        trail_file = tmp_path / "decision_trail.json"
        assert trail_file.exists()
        data = json.loads(trail_file.read_text())
        assert data["status"] == "failed"

    async def test_no_fingerprint(self, tmp_path):
        with (
            patch("oprim.template_render.template_render", return_value="body"),
            patch("oprim.push_email.push_email", return_value=_email_result()),
            patch("obase.persistence.pool.PgPool.get_or_create", new_callable=AsyncMock, return_value=MagicMock()),
            patch("omodul.reset_password_workflow.write_one", new_callable=AsyncMock, return_value=1),
        ):
            result = await reset_password_workflow(_reset_config(), ResetPasswordInput(), tmp_path)
        assert result["fingerprint"] is None

    async def test_decision_trail_has_steps(self, tmp_path):
        with (
            patch("oprim.template_render.template_render", return_value="body"),
            patch("oprim.push_email.push_email", return_value=_email_result()),
            patch("obase.persistence.pool.PgPool.get_or_create", new_callable=AsyncMock, return_value=MagicMock()),
            patch("omodul.reset_password_workflow.write_one", new_callable=AsyncMock, return_value=1),
        ):
            result = await reset_password_workflow(_reset_config(), ResetPasswordInput(), tmp_path)
        assert len(result["decision_trail"]["steps"]) >= 2

    async def test_cost_is_zero(self, tmp_path):
        with (
            patch("oprim.template_render.template_render", return_value="body"),
            patch("oprim.push_email.push_email", return_value=_email_result()),
            patch("obase.persistence.pool.PgPool.get_or_create", new_callable=AsyncMock, return_value=MagicMock()),
            patch("omodul.reset_password_workflow.write_one", new_callable=AsyncMock, return_value=1),
        ):
            result = await reset_password_workflow(_reset_config(), ResetPasswordInput(), tmp_path)
        assert result["cost_usd"] == 0.0


# ---------------------------------------------------------------------------
# omodul-026: verify_email_workflow
# ---------------------------------------------------------------------------


class TestVerifyEmailWorkflow:
    async def test_send_action_sent_true_and_otp_secret_returned(self, tmp_path):
        otp = _otp_result()
        with (
            patch("oprim.otp_generate.otp_generate", return_value=otp),
            patch("oprim.template_render.template_render", return_value="Your code is: 123456"),
            patch("oprim.push_email.push_email", return_value=_email_result()),
        ):
            result = await verify_email_workflow(
                _verify_config(action="send"),
                VerifyEmailInput(),
                tmp_path,
            )
        assert result["status"] == "completed"
        assert result["findings"].sent is True
        assert result["findings"].otp_secret == "JBSWY3DPEHPK3PXP"

    async def test_verify_valid_otp_verified_true(self, tmp_path):
        with (
            patch("oprim.otp_generate.otp_verify", return_value=True),
            patch("obase.persistence.pool.PgPool.get_or_create", new_callable=AsyncMock, return_value=MagicMock()),
            patch("omodul.verify_email_workflow.update_one", new_callable=AsyncMock, return_value=True),
        ):
            result = await verify_email_workflow(
                _verify_config(action="verify"),
                VerifyEmailInput(otp_secret="JBSWY3DPEHPK3PXP", otp_code="123456"),
                tmp_path,
            )
        assert result["status"] == "completed"
        assert result["findings"].verified is True

    async def test_verify_invalid_otp_verified_false(self, tmp_path):
        with patch("oprim.otp_generate.otp_verify", return_value=False):
            result = await verify_email_workflow(
                _verify_config(action="verify"),
                VerifyEmailInput(otp_secret="JBSWY3DPEHPK3PXP", otp_code="000000"),
                tmp_path,
            )
        assert result["status"] == "completed"
        assert result["findings"].verified is False

    async def test_missing_otp_for_verify_status_failed(self, tmp_path):
        result = await verify_email_workflow(
            _verify_config(action="verify"),
            VerifyEmailInput(),  # no otp_secret or otp_code
            tmp_path,
        )
        assert result["status"] == "failed"
        assert "otp_secret" in result["error"]["error_message"]

    async def test_fingerprint_present(self, tmp_path):
        otp = _otp_result()
        with (
            patch("oprim.otp_generate.otp_generate", return_value=otp),
            patch("oprim.template_render.template_render", return_value="code body"),
            patch("oprim.push_email.push_email", return_value=_email_result()),
        ):
            result = await verify_email_workflow(
                _verify_config(action="send"),
                VerifyEmailInput(),
                tmp_path,
            )
        assert result["fingerprint"] is not None
        assert len(result["fingerprint"]) == 64

    async def test_send_smtp_failure_status_failed(self, tmp_path):
        otp = _otp_result()
        with (
            patch("oprim.otp_generate.otp_generate", return_value=otp),
            patch("oprim.template_render.template_render", return_value="code body"),
            patch("oprim.push_email.push_email", side_effect=OSError("smtp down")),
        ):
            result = await verify_email_workflow(
                _verify_config(action="send"),
                VerifyEmailInput(),
                tmp_path,
            )
        assert result["status"] == "failed"

    async def test_decision_trail_is_none(self, tmp_path):
        otp = _otp_result()
        with (
            patch("oprim.otp_generate.otp_generate", return_value=otp),
            patch("oprim.template_render.template_render", return_value="code body"),
            patch("oprim.push_email.push_email", return_value=_email_result()),
        ):
            result = await verify_email_workflow(
                _verify_config(action="send"),
                VerifyEmailInput(),
                tmp_path,
            )
        assert result["decision_trail"] is None

    async def test_cost_is_zero(self, tmp_path):
        otp = _otp_result()
        with (
            patch("oprim.otp_generate.otp_generate", return_value=otp),
            patch("oprim.template_render.template_render", return_value="code body"),
            patch("oprim.push_email.push_email", return_value=_email_result()),
        ):
            result = await verify_email_workflow(
                _verify_config(action="send"),
                VerifyEmailInput(),
                tmp_path,
            )
        assert result["cost_usd"] == 0.0
