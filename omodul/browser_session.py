"""Browser session preparation transaction.

The transaction sequences browser lifecycle atomics after a computer has been
prepared by the existing Computer Supervisor.  It owns no driver, policy,
approval store, or browser registry.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, ClassVar

from obase.browser import BrowserProfile
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
    browser: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return build_result(
        status="failed",
        error={"type": "BrowserNotReady", "message": error},
        fingerprint=fingerprint,
        trail=trail,
        computer=dict(computer or {}),
        browser=dict(browser or {}),
        prepared=False,
    )


class PrepareBrowserSessionConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "prepare_browser_session"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "decision_trail"}


class PrepareBrowserSessionInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    profile: BrowserProfile
    computer: Mapping[str, Any]
    browser_create: Any
    browser_start: Any
    browser_status: Any
    browser_attach: Any | None = None
    attach: bool = False


async def prepare_browser_session(
    config: PrepareBrowserSessionConfig,
    input_data: PrepareBrowserSessionInput,
    output_dir: Path,
    *,
    on_step: Callable[[dict[str, Any]], Any] | None = None,
) -> dict[str, Any]:
    """Create, start, inspect, and optionally attach one browser session."""
    del config
    output_dir.mkdir(parents=True, exist_ok=True)
    trail = Trail()
    fingerprint = compute_fingerprint(
        {"profile": input_data.profile.to_dict(), "computer": dict(input_data.computer)}
    )
    computer = dict(input_data.computer)

    def step(event: str, **detail: Any) -> None:
        trail.record(event=event, **detail)
        if on_step is not None:
            on_step({"event": event, **detail})

    try:
        step("browser_create", computer_id=input_data.profile.computer_id)
        created = await _call(input_data.browser_create, input_data.profile)
        if not isinstance(created, Mapping) or not created.get("ok"):
            return _failed(
                error=str((created or {}).get("error") or "browser create failed"),
                fingerprint=fingerprint,
                trail=trail,
                computer=computer,
            )
        handle = created.get("handle") or created.get("browser")
        if not isinstance(handle, Mapping):
            return _failed(
                error="browser create returned no handle",
                fingerprint=fingerprint,
                trail=trail,
                computer=computer,
            )

        step("browser_start", session_id=handle.get("session_id", ""))
        started = await _call(input_data.browser_start, handle)
        if not isinstance(started, Mapping) or not started.get("ok"):
            return _failed(
                error=str((started or {}).get("error") or "browser start failed"),
                fingerprint=fingerprint,
                trail=trail,
                computer=computer,
                browser=handle,
            )
        handle = started.get("handle") or started.get("browser") or handle

        step("browser_status", session_id=handle.get("session_id", ""))
        status = await _call(input_data.browser_status, handle)
        if not isinstance(status, Mapping) or not status.get("ok"):
            return _failed(
                error=str((status or {}).get("error") or "browser status failed"),
                fingerprint=fingerprint,
                trail=trail,
                computer=computer,
                browser=handle,
            )
        handle = status.get("handle") or status.get("browser") or handle
        if str(handle.get("state", status.get("status", ""))) not in {"running", "attached"}:
            return _failed(
                error="browser session is not running",
                fingerprint=fingerprint,
                trail=trail,
                computer=computer,
                browser=handle,
            )

        if input_data.attach:
            if input_data.browser_attach is None:
                return _failed(
                    error="attach requested but browser_attach is not configured",
                    fingerprint=fingerprint,
                    trail=trail,
                    computer=computer,
                    browser=handle,
                )
            step("browser_attach", session_id=handle.get("session_id", ""))
            attached = await _call(input_data.browser_attach, handle)
            if not isinstance(attached, Mapping) or not attached.get("ok"):
                return _failed(
                    error=str((attached or {}).get("error") or "browser attach failed"),
                    fingerprint=fingerprint,
                    trail=trail,
                    computer=computer,
                    browser=handle,
                )
            handle = attached.get("handle") or attached.get("browser") or handle

        return build_result(
            status="completed",
            fingerprint=fingerprint,
            trail=trail,
            computer=computer,
            browser=dict(handle),
            prepared=True,
        )
    except Exception as exc:
        return _failed(
            error=f"{type(exc).__name__}: {exc}",
            fingerprint=fingerprint,
            trail=trail,
            computer=computer,
        )


__all__ = [
    "PrepareBrowserSessionConfig",
    "PrepareBrowserSessionInput",
    "prepare_browser_session",
]
