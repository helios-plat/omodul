"""omodul.reset_user_password — 生成密码重置 token,发通知。

只做"发起重置"这一步(生成 token + 通知),不改密码——SPEC 只列了这一个
元素,没有配套的"用 token 确认新密码"步骤,那部分不在本轮范围内(留空白
比自己臆造一个未经确认的接口更诚实)。

范围声明(自由裁量):没用 obase.auth.jwt.jwt_create——那需要一个签名密钥,
项目里还没有统一的密钥管理/配置来源,强行引入一个环境变量名字是在猜。改用
secrets.token_urlsafe 生成不透明随机 token,存 app_user.reset_token +
reset_token_expires_at(1 小时有效期),足够满足"生成 token"的语义,且不
引入未经确认的密钥配置假设。

Composes:
  - secrets.token_urlsafe(生成 token)
  - obase.provider_registry.ProviderRegistry(取 "notification" category 下的
    provider 调 send_email)

Pillars: decision_trail
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result


class ResetUserPasswordConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "reset_user_password"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail"}

    token_ttl_minutes: int = 60


class ResetUserPasswordInput(BaseModel):
    user_id: str
    notification_provider_name: str = "log"


async def reset_user_password(
    config: ResetUserPasswordConfig,
    input_data: ResetUserPasswordInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """生成重置 token,存到 app_user,并通过通知 provider 发给用户邮箱。

    Args:
        config: ResetUserPasswordConfig。
        input_data: user_id / notification_provider_name。
        output_dir: decision_trail 落盘目录。
        pool: obase.persistence.PgPool。本函数必须落库,pool 为 None 直接判 failed。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict;findings 含 token 过期时间(token 本身不回传,
        只通过通知渠道下发,避免明文 token 出现在返回值/日志里)。
    """
    from obase.persistence import read_one, update_one
    from obase.provider_registry import ProviderRegistry

    trail = Trail()

    try:
        if pool is None:
            raise ValueError("pool is required — reset_user_password always touches persisted user")

        user = await read_one(pool, table="app_user", id=input_data.user_id)
        if user is None:
            raise ValueError(f"user {input_data.user_id} not found")

        token = secrets.token_urlsafe(32)
        expires_at = datetime.now(UTC) + timedelta(minutes=config.token_ttl_minutes)

        await update_one(
            pool,
            table="app_user",
            id=input_data.user_id,
            data={"reset_token": token, "reset_token_expires_at": expires_at},
        )
        trail.record(event="reset_token_generated", user_id=input_data.user_id)

        provider = ProviderRegistry.get().generic(
            "notification", input_data.notification_provider_name
        )
        await provider.send_email(
            to=user["email"],
            subject="Password reset",
            body=f"Your password reset token: {token} (expires in "
            f"{config.token_ttl_minutes} minutes)",
        )
        trail.record(event="notification_sent", provider_name=input_data.notification_provider_name)

        if on_step:
            on_step(
                {
                    "stage": "reset_user_password",
                    "status": "done",
                    "user_id": input_data.user_id,
                }
            )

        trail_path = trail.write(Path(output_dir)) if output_dir else None

        return build_result(
            status="completed",
            error=None,
            trail=trail,
            trail_path=trail_path,
            user_id=input_data.user_id,
            expires_at=expires_at.isoformat(),
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
