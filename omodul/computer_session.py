"""Computer session preparation transaction.

The transaction sequences injected lifecycle atomics and a stateless
readiness evaluator.  It owns no process, Docker client, workspace, or
computer registry.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, ClassVar

from obase.computer import ComputerProfile
from pydantic import BaseModel, ConfigDict

from omodul._base import BaseConfig, Trail, build_result, compute_fingerprint


async def _call(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    result = fn(*args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


def _failed(
    *,
    error: str,
    fingerprint: str,
    trail: Trail,
    computer: Mapping[str, Any] | None = None,
    readiness: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return build_result(
        status="failed",
        error={"type": "ComputerNotReady", "message": error},
        fingerprint=fingerprint,
        trail=trail,
        computer=dict(computer or {}),
        readiness=dict(readiness or {}),
    )


class PrepareComputerSessionConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "prepare_computer_session"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "decision_trail"}


class PrepareComputerSessionInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    profile: ComputerProfile
    computer_create: Any
    computer_start: Any
    computer_status: Any
    readiness_evaluator: Any
    computer_attach: Any | None = None
    attach: bool = False


async def prepare_computer_session(
    config: PrepareComputerSessionConfig,
    input_data: PrepareComputerSessionInput,
    output_dir: Path,
    *,
    on_step: Callable[[dict[str, Any]], Any] | None = None,
) -> dict[str, Any]:
    """Create, start, inspect, and optionally attach a computer session."""
    del config
    output_dir.mkdir(parents=True, exist_ok=True)
    trail = Trail()
    fingerprint = compute_fingerprint({"profile": input_data.profile.to_dict()})

    def step(event: str, **detail: Any) -> None:
        trail.record(event=event, **detail)
        if on_step is not None:
            on_step({"event": event, **detail})

    try:
        step("computer_create", profile_id=input_data.profile.id)
        created = await _call(input_data.computer_create, input_data.profile)
        if not isinstance(created, Mapping) or not created.get("ok"):
            return _failed(
                error=str((created or {}).get("error") or "computer create failed"),
                fingerprint=fingerprint,
                trail=trail,
            )
        handle = created.get("handle") or created.get("computer")
        if not isinstance(handle, Mapping):
            return _failed(
                error="computer create returned no handle",
                fingerprint=fingerprint,
                trail=trail,
            )

        step("computer_start", computer_id=handle.get("computer_id", ""))
        started = await _call(input_data.computer_start, handle)
        if not isinstance(started, Mapping) or not started.get("ok"):
            return _failed(
                error=str((started or {}).get("error") or "computer start failed"),
                fingerprint=fingerprint,
                trail=trail,
                computer=handle,
            )
        handle = started.get("handle") or started.get("computer") or handle

        step("computer_status", computer_id=handle.get("computer_id", ""))
        status = await _call(input_data.computer_status, handle)
        if not isinstance(status, Mapping) or not status.get("ok"):
            return _failed(
                error=str((status or {}).get("error") or "computer status failed"),
                fingerprint=fingerprint,
                trail=trail,
                computer=handle,
            )
        handle = status.get("handle") or status.get("computer") or handle

        step("computer_readiness", computer_id=handle.get("computer_id", ""))
        readiness = await _call(
            input_data.readiness_evaluator,
            handle,
            status=status,
        )
        if not isinstance(readiness, Mapping) or not readiness.get("ready"):
            return _failed(
                error=str((readiness or {}).get("reason") or "computer is not ready"),
                fingerprint=fingerprint,
                trail=trail,
                computer=handle,
                readiness=readiness if isinstance(readiness, Mapping) else None,
            )

        if input_data.attach:
            if input_data.computer_attach is None:
                return _failed(
                    error="attach requested but computer_attach is not configured",
                    fingerprint=fingerprint,
                    trail=trail,
                    computer=handle,
                    readiness=readiness,
                )
            step("computer_attach", computer_id=handle.get("computer_id", ""))
            attached = await _call(input_data.computer_attach, handle)
            if not isinstance(attached, Mapping) or not attached.get("ok"):
                return _failed(
                    error=str((attached or {}).get("error") or "computer attach failed"),
                    fingerprint=fingerprint,
                    trail=trail,
                    computer=handle,
                    readiness=readiness,
                )
            handle = attached.get("handle") or attached.get("computer") or handle

        return build_result(
            status="completed",
            fingerprint=fingerprint,
            trail=trail,
            computer=dict(handle),
            readiness=dict(readiness),
            prepared=True,
        )
    except Exception as exc:
        return _failed(
            error=f"{type(exc).__name__}: {exc}",
            fingerprint=fingerprint,
            trail=trail,
        )


__all__ = [
    "PrepareComputerSessionConfig",
    "PrepareComputerSessionInput",
    "prepare_computer_session",
]
