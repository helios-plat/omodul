"""omodul.user_data_workflow — User data management workflow.

Handles user record creation, querying, and deletion (GDPR-like).
After deletion, user records are not queryable.

Pillars: fingerprint + decision_trail
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, CostTracker, Trail, build_result, compute_fingerprint


class UserDataConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "user_data_workflow"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "decision_trail"}
    _fingerprint_fields: ClassVar[set[str]] = {"user_id", "operation"}

    soft_delete: bool = True


class UserRecord(BaseModel):
    user_id: str
    display_name: str = ""
    email: str = ""
    created_at: float = 0.0
    deleted: bool = False
    deleted_at: float | None = None
    metadata: dict = {}


class UserDataInput(BaseModel):
    user_id: str
    operation: str = "query"
    record: UserRecord | None = None


_USER_STORE: dict[str, UserRecord] = {}


def _get_store() -> dict[str, UserRecord]:
    return _USER_STORE


def reset_store() -> None:
    """Reset in-memory store (for testing)."""
    _USER_STORE.clear()


async def user_data_workflow(
    config: UserDataConfig,
    input_data: UserDataInput,
    output_dir: Path,
    *,
    store: dict[str, UserRecord] | None = None,
    on_step: Any = None,
) -> dict:
    cost = CostTracker()
    trail = Trail()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if store is None:
        store = _get_store()

    try:
        trail.record(event="start", user_id=input_data.user_id, op=input_data.operation)
        fp = compute_fingerprint({"user_id": input_data.user_id, "operation": input_data.operation})

        op = input_data.operation.lower()

        if op == "create":
            if input_data.record is None:
                raise ValueError("record required for create operation")
            rec = input_data.record.model_copy()
            if rec.created_at == 0.0:
                rec = rec.model_copy(update={"created_at": time.time()})
            store[input_data.user_id] = rec
            trail.record(event="created")
            if on_step:
                on_step("user_data_workflow", "created")
            return build_result(
                status="ok",
                fingerprint=fp,
                trail=trail,
                trail_path=trail.write(output_dir),
                cost_usd=0.0,
                user_id=input_data.user_id,
                record=rec.model_dump(),
            )

        elif op == "query":
            rec = store.get(input_data.user_id)
            if rec is None or rec.deleted:
                trail.record(event="not_found")
                return build_result(
                    status="ok",
                    fingerprint=fp,
                    trail=trail,
                    trail_path=trail.write(output_dir),
                    cost_usd=0.0,
                    user_id=input_data.user_id,
                    record=None,
                    found=False,
                )
            trail.record(event="found")
            return build_result(
                status="ok",
                fingerprint=fp,
                trail=trail,
                trail_path=trail.write(output_dir),
                cost_usd=0.0,
                user_id=input_data.user_id,
                record=rec.model_dump(),
                found=True,
            )

        elif op == "delete":
            rec = store.get(input_data.user_id)
            if rec is None:
                trail.record(event="delete_noop")
                return build_result(
                    status="ok",
                    cost_usd=0.0,
                    user_id=input_data.user_id,
                    deleted=False,
                    message="User not found",
                )
            if config.soft_delete:
                updated = rec.model_copy(update={"deleted": True, "deleted_at": time.time()})
                store[input_data.user_id] = updated
            else:
                del store[input_data.user_id]
            trail.record(event="deleted", soft=config.soft_delete)
            if on_step:
                on_step("user_data_workflow", "deleted")
            return build_result(
                status="ok",
                fingerprint=fp,
                trail=trail,
                trail_path=trail.write(output_dir),
                cost_usd=0.0,
                user_id=input_data.user_id,
                deleted=True,
            )

        else:
            raise ValueError(f"Unknown operation: {op}")

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="error",
            error={"type": type(exc).__name__, "message": str(exc)},
            cost_usd=0.0,
        )
