"""omodul.update_user — 局部更新管理员账号(email/password/name/status)。

密码若更新,重新 bcrypt 哈希;不接受直接传 password_hash(避免绕过哈希)。

Pillars: decision_trail
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result


class UpdateUserConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "update_user"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail"}


class UpdateUserInput(BaseModel):
    user_id: str
    email: str | None = None
    password: str | None = None
    name: str | None = None
    status: str | None = None


async def update_user(
    config: UpdateUserConfig,
    input_data: UpdateUserInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """局部更新管理员账号。

    Args:
        config: UpdateUserConfig。
        input_data: user_id + 任意子集的可更新字段。
        output_dir: decision_trail 落盘目录。
        pool: obase.persistence.PgPool。本函数必须落库,pool 为 None 直接判 failed。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict。
    """
    from obase.auth.password import bcrypt_hash
    from obase.persistence import read_one, update_one

    trail = Trail()

    try:
        if pool is None:
            raise ValueError("pool is required — update_user always touches persisted user")

        if input_data.password is not None and len(input_data.password) < 8:
            raise ValueError("password must be at least 8 characters")

        updates: dict[str, Any] = {}
        if input_data.email is not None:
            updates["email"] = input_data.email
        if input_data.password is not None:
            updates["password_hash"] = bcrypt_hash(password=input_data.password)
        if input_data.name is not None:
            updates["name"] = input_data.name
        if input_data.status is not None:
            updates["status"] = input_data.status
        if not updates:
            raise ValueError("at least one field must be provided to update")

        user = await read_one(pool, table="app_user", id=input_data.user_id)
        if user is None:
            raise ValueError(f"user {input_data.user_id} not found")

        await update_one(pool, table="app_user", id=input_data.user_id, data=updates)
        # Never echo password_hash into the trail even in redacted form.
        trail.record(
            event="user_updated",
            user_id=input_data.user_id,
            fields=[f if f != "password_hash" else "password" for f in updates],
        )

        if on_step:
            on_step({"stage": "update_user", "status": "done", "user_id": input_data.user_id})

        trail_path = trail.write(Path(output_dir)) if output_dir else None

        return build_result(
            status="completed",
            error=None,
            trail=trail,
            trail_path=trail_path,
            user_id=input_data.user_id,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
