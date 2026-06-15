"""
认证与用户管理业务事务
======================
omodul/auth.py
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from obase.auth import create_access_token, decode_access_token  # noqa: F401
from obase.sms import send_otp, verify_otp
from omodul.base import BaseConfig, standard_return


class AuthConfig(BaseConfig):
    _omodul_name = "auth_workflow"
    _omodul_version = "0.1.0"
    _enabled_pillars = {"decision_trail"}


class SendCodeInput(BaseModel):
    phone: str = Field(..., pattern=r"^1[3-9]\d{9}$")


class RegisterStudentInput(BaseModel):
    phone: str = Field(..., pattern=r"^1[3-9]\d{9}$")
    code: str
    name: str
    birth_date: date
    grade: str
    guardian_phone: Optional[str] = None
    guardian_consent: bool = False


class LoginInput(BaseModel):
    phone: str
    code: str


async def send_code_workflow(config: AuthConfig, input_data: SendCodeInput) -> dict:
    """发送短信验证码。"""
    code = await send_otp(input_data.phone)
    return standard_return(
        findings={"ok": True, "message": "Code sent (dev mock)"},
        status="completed",
        trail=[{"step": "send_otp", "phone": input_data.phone, "mock_code": code}]
        if "decision_trail" in config._enabled_pillars else None,
    )


async def register_student_workflow(
    config: AuthConfig,
    input_data: RegisterStudentInput,
    db: AsyncSession,
) -> dict:
    """注册学生，含合规校验（<14岁须监护人同意）。"""
    from services.models import GuardianConsent, User, UserRole  # lazy import avoids cycle

    # 合规：年龄校验
    today = datetime.now(timezone.utc).date()
    age = (today - input_data.birth_date).days // 365
    if age < 14:
        if not input_data.guardian_phone or not input_data.guardian_consent:
            return standard_return(
                findings={"error_code": 422},
                status="failed",
                error="Guardian consent required for students under 14",
            )

    # OTP 校验
    ok = await verify_otp(input_data.phone, input_data.code)
    if not ok:
        return standard_return(findings={"error_code": 400}, status="failed", error="Invalid code")

    # 写用户
    user = User(
        id=uuid.uuid4(),
        phone=input_data.phone,
        name=input_data.name,
        role=UserRole.student,
        grade=input_data.grade,
    )
    db.add(user)
    await db.flush()

    if age < 14 and input_data.guardian_phone:
        consent = GuardianConsent(
            id=uuid.uuid4(),
            student_id=user.id,
            guardian_phone=input_data.guardian_phone,
            consent_type="registration",
            consent_version="1.0",
        )
        db.add(consent)
        await db.flush()

    token = create_access_token({"sub": str(user.id), "role": user.role.value})
    return standard_return(
        findings={"token": token, "user": {"id": str(user.id), "name": user.name, "phone": user.phone}},
        status="completed",
    )


async def login_workflow(
    config: AuthConfig,
    input_data: LoginInput,
    db: AsyncSession,
) -> dict:
    """登录，返回 JWT token。"""
    from services.models import User  # lazy import

    ok = await verify_otp(input_data.phone, input_data.code)
    if not ok:
        return standard_return(findings=None, status="failed", error="Invalid code")

    user = (
        await db.execute(select(User).where(User.phone == input_data.phone))
    ).scalar_one_or_none()
    if not user:
        return standard_return(findings=None, status="failed", error="User not found")

    token = create_access_token({"sub": str(user.id), "role": user.role.value})
    return standard_return(
        findings={"token": token, "user": {"id": str(user.id), "name": user.name}},
        status="completed",
    )
