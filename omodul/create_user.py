"""omodul.create_user — 新建后台管理员账号。高审计:创建即挂 decision_trail。

Composes:
  - obase.auth.password.bcrypt_hash(密码哈希,明文密码从不落库)
  - obase.uuid7.uuid7 + obase.persistence.insert_one

Pillars: decision_trail
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result


class CreateUserConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "create_user"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail"}


class CreateUserInput(BaseModel):
    email: str
    password: str
    name: str = ""


async def create_user(
    config: CreateUserConfig,
    input_data: CreateUserInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """新建管理员账号,密码经 bcrypt 哈希后落库(明文密码不进 decision_trail)。

    Args:
        config: CreateUserConfig。
        input_data: email / password / name。
        output_dir: decision_trail 落盘目录。
        pool: obase.persistence.PgPool;为 None 时跳过持久化(dry-run,便于单测)。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict,findings 中含 user_id。
    """
    from obase.auth.password import bcrypt_hash
    from obase.persistence import insert_one
    from obase.uuid7 import uuid7

    trail = Trail()

    try:
        if not input_data.email:
            raise ValueError("email is required")
        if len(input_data.password) < 8:
            raise ValueError("password must be at least 8 characters")

        trail.record(event="validated", email=input_data.email)

        user_id = uuid7()
        row = {
            "id": user_id,
            "email": input_data.email,
            "password_hash": bcrypt_hash(password=input_data.password),
            "name": input_data.name or None,
        }

        if pool is not None:
            await insert_one(pool, table="app_user", data=row)
            trail.record(event="persisted", user_id=user_id)
        else:
            trail.record(event="persisted_skipped_no_pool", user_id=user_id)

        if on_step:
            on_step({"stage": "create_user", "status": "done", "user_id": user_id})

        trail_path = trail.write(Path(output_dir)) if output_dir else None

        return build_result(
            status="completed",
            error=None,
            trail=trail,
            trail_path=trail_path,
            user_id=user_id,
            email=input_data.email,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
