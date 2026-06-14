"""
omodul.login_provider — Authenticate with an LLM provider via API key or OAuth.

Pillars: decision_trail
"""
from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from omodul._base import BaseConfig, Trail, build_result


async def _call(fn: Any, **kwargs: Any) -> Any:
    result = fn(**kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


class Config(BaseConfig):
    _omodul_name: ClassVar[str] = "login_provider"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail"}


class InputData(BaseModel):
    provider: str
    auth_mode: str = "api_key"
    api_key: str = ""
    validator: Any = None

    model_config = ConfigDict(arbitrary_types_allowed=True)


async def login_provider(
    config: Config,
    input_data: InputData,
    output_dir: Path,
    *,
    on_step=None,
) -> dict:
    """Authenticate with a provider; validates API key or initiates OAuth flow."""
    trail = Trail()

    try:
        trail.record(event="start_login", step_no=0, provider=input_data.provider,
                     auth_mode=input_data.auth_mode)

        if input_data.auth_mode == "api_key":
            if input_data.validator is not None:
                valid = await _call(input_data.validator,
                                    key=input_data.api_key,
                                    provider=input_data.provider)
            else:
                valid = bool(input_data.api_key)

            if not valid:
                trail.record(event="api_key_rejected", step_no=1)
                loop = asyncio.get_event_loop()
                await asyncio.shield(
                    loop.run_in_executor(None, trail.write, output_dir)
                )
                return build_result(
                    status="failed",
                    error={"type": "AuthError", "message": "invalid api key"},
                    trail=trail,
                )

            trail.record(event="api_key_validated", step_no=1)

        elif input_data.auth_mode == "oauth":
            trail.record(event="oauth_initiated", step_no=1,
                         oauth_url=f"https://auth.{input_data.provider}.com/oauth")
        else:
            trail.record(event="unknown_auth_mode", step_no=1,
                         auth_mode=input_data.auth_mode)

        loop = asyncio.get_event_loop()
        await asyncio.shield(loop.run_in_executor(None, trail.write, output_dir))

        return build_result(
            status="completed",
            error=None,
            trail=trail,
            provider=input_data.provider,
            auth_mode=input_data.auth_mode,
        )

    except asyncio.CancelledError:
        raise
    except Exception as exc:
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
            trail=trail,
        )
