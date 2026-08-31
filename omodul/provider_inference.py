"""Canonical provider inference transaction.

Provider selection and transport are injected.  This module sequences one
inference transaction, preserves provider events, records normalized usage,
and applies only the explicit fallback policy supplied by the caller.
"""

from __future__ import annotations

import inspect
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, ClassVar

from obase.provider_routing import ProviderCallRequest, UsageRecord
from pydantic import BaseModel, ConfigDict, Field

from omodul._base import BaseConfig, Trail, build_result, compute_fingerprint


async def _call(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    result = fn(*args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


def _field(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _tokens(value: Any) -> tuple[int, int]:
    usage = value.get("usage", {}) if isinstance(value, Mapping) else {}
    if not isinstance(usage, Mapping):
        usage = {}
    return (
        int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0),
        int(usage.get("output_tokens") or usage.get("completion_tokens") or 0),
    )


def _is_error(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    return bool(value.get("error")) or value.get("ok") is False or value.get("status") == "error"


def _event_usage(events: list[Any]) -> tuple[int, int]:
    input_tokens = output_tokens = 0
    for event in events:
        in_tok, out_tok = _tokens(event)
        input_tokens = max(input_tokens, in_tok)
        output_tokens = max(output_tokens, out_tok)
        if isinstance(event, Mapping) and event.get("type") == "usage":
            usage = event.get("usage", event)
            if isinstance(usage, Mapping):
                input_tokens = max(
                    input_tokens,
                    int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0),
                )
                output_tokens = max(
                    output_tokens,
                    int(usage.get("output_tokens") or usage.get("completion_tokens") or 0),
                )
    return input_tokens, output_tokens


def _model_pricing(candidates: list[Any], selection: Mapping[str, Any]) -> Any:
    for provider in candidates:
        if str(_field(provider, "name", "")) != str(selection.get("provider", "")):
            continue
        for model in _field(provider, "models", ()) or ():
            if str(_field(model, "name", "")) == str(selection.get("model", "")):
                return _field(model, "pricing")
    return None


def _estimate_cost(pricing: Any, input_tokens: int, output_tokens: int) -> float | None:
    if pricing is None:
        return None
    input_price = _field(pricing, "input_usd_per_token")
    output_price = _field(pricing, "output_usd_per_token")
    if input_price is None or output_price is None:
        return None
    return input_tokens * float(input_price) + output_tokens * float(output_price)


class ProviderInferenceConfig(BaseConfig):
    """Configuration for one provider inference transaction."""

    llm_provider: str = ""
    llm_model: str = ""
    capability: str = "chat"
    streaming: bool = False
    max_attempts: int = 2
    fallback_policy: dict[str, Any] = Field(default_factory=dict)
    preferred_provider: str | None = None
    strict_pricing: bool = False

    _omodul_name: ClassVar[str] = "provider_inference_transaction"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "decision_trail", "cost"}


class ProviderInferenceInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    messages: list[dict[str, Any]]
    candidates: list[Any] = Field(default_factory=list)
    provider_call: Any
    select_provider: Any
    fallback_decision: Any
    usage_record: Any | None = None
    usage_sink: Any | None = None
    provider_caller: Any | None = None
    tools: list[dict[str, Any]] | None = None
    max_tokens: int | None = None
    temperature: float | None = None
    request_ref: str | None = None


async def _record_usage(
    input_data: ProviderInferenceInput,
    record: UsageRecord,
) -> Any:
    if input_data.usage_record is None:
        return record.to_dict()
    if input_data.usage_sink is None:
        return await _call(input_data.usage_record, record)
    return await _call(input_data.usage_record, record, sink=input_data.usage_sink)


async def provider_inference_transaction(
    config: ProviderInferenceConfig,
    input_data: ProviderInferenceInput,
    output_dir: Path,
    *,
    on_step: Callable[[dict[str, Any]], Any] | None = None,
) -> dict[str, Any]:
    """Select, call, optionally fail over, and record one inference."""
    output_dir.mkdir(parents=True, exist_ok=True)
    trail = Trail()
    candidates = list(input_data.candidates)
    fingerprint = compute_fingerprint(
        {
            "provider": config.llm_provider,
            "model": config.llm_model,
            "capability": config.capability,
            "streaming": config.streaming,
            "messages": input_data.messages,
            "tools": input_data.tools or [],
        }
    )
    attempts: list[dict[str, Any]] = []
    excluded: list[str] = []

    def step(event: str, **detail: Any) -> None:
        trail.record(event=event, **detail)
        if on_step is not None:
            on_step({"event": event, **detail})

    if not candidates:
        return build_result(
            status="failed",
            error={"type": "NoProviderCandidates", "message": "provider candidates are empty"},
            fingerprint=fingerprint,
            trail=trail,
            attempts=attempts,
        )

    for attempt_no in range(max(1, config.max_attempts)):
        selection: dict[str, Any] | None = None
        call_started: float | None = None
        try:
            raw_selection = await _call(
                input_data.select_provider,
                candidates,
                capability=config.capability,
                model=config.llm_model or None,
                streaming=config.streaming,
                tools=bool(input_data.tools),
                preferred_provider=config.preferred_provider or config.llm_provider or None,
                exclude=excluded,
            )
            if not isinstance(raw_selection, Mapping):
                raise TypeError("select_provider must return a mapping")
            selection = dict(raw_selection)
            provider = str(selection.get("provider", ""))
            model = str(selection.get("model", ""))
            if not provider or not model:
                raise ValueError("provider selection is missing provider or model")
            excluded.append(provider)
            step("provider_selected", provider=provider, model=model, attempt=attempt_no + 1)
            request = ProviderCallRequest(
                provider=provider,
                model=model,
                messages=tuple(input_data.messages),
                tools=tuple(input_data.tools or []),
                stream=config.streaming,
                max_tokens=input_data.max_tokens,
                temperature=input_data.temperature,
                credential_ref=selection.get("credential_ref"),
                request_ref=input_data.request_ref,
            )
            call_started = time.monotonic()
            step("provider_call", provider=provider, model=model, stream=config.streaming)
            if input_data.provider_caller is None:
                response = await _call(input_data.provider_call, request)
            else:
                response = await _call(
                    input_data.provider_call,
                    request,
                    caller=input_data.provider_caller,
                )
            latency_ms = (time.monotonic() - call_started) * 1000.0

            events: list[Any] | None = None
            if config.streaming and hasattr(response, "__aiter__"):
                events = []
                async for event in response:
                    events.append(event)
                    if isinstance(event, Mapping):
                        step(
                            "provider_event",
                            provider=provider,
                            event_type=str(event.get("type", "")),
                            data=event,
                        )
                input_tokens, output_tokens = _event_usage(events)
                response_value: Any = {"events": events}
            else:
                input_tokens, output_tokens = _tokens(response)
                response_value = response
            error = _is_error(response)
            record = UsageRecord(
                provider=provider,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=latency_ms,
                estimated_cost_usd=_estimate_cost(
                    _model_pricing(candidates, selection), input_tokens, output_tokens
                ),
                success=not error,
                streamed=config.streaming,
                request_ref=input_data.request_ref,
                error_type="ProviderResponseError" if error else None,
            )
            try:
                await _record_usage(input_data, record)
            except Exception as usage_exc:  # noqa: BLE001 - usage failure is not provider fallback
                return build_result(
                    status="failed",
                    error={"type": "UsageRecordFailed", "message": str(usage_exc)},
                    fingerprint=fingerprint,
                    trail=trail,
                    attempts=attempts,
                    selection=selection,
                    usage=record.to_dict(),
                    response=response_value,
                    events=events,
                    cost_usd=record.estimated_cost_usd or 0.0,
                )
            step("usage_recorded", provider=provider, model=model, total_tokens=record.total_tokens)
            attempts.append({"provider": provider, "model": model, "success": not error})
            if error:
                attempts[-1]["error_type"] = "ProviderResponseError"
            if not error:
                if config.strict_pricing and record.estimated_cost_usd is None:
                    return build_result(
                        status="failed",
                        error={
                            "type": "PricingNotConfigured",
                            "message": "model pricing is unavailable",
                        },
                        fingerprint=fingerprint,
                        trail=trail,
                        attempts=attempts,
                        selection=selection,
                        usage=record.to_dict(),
                        response=response_value,
                        cost_usd=record.estimated_cost_usd or 0.0,
                    )
                return build_result(
                    status="completed",
                    error=None,
                    fingerprint=fingerprint,
                    trail=trail,
                    attempts=attempts,
                    selection=selection,
                    usage=record.to_dict(),
                    response=response_value,
                    events=events,
                    cost_usd=record.estimated_cost_usd or 0.0,
                )
        except Exception as exc:  # noqa: BLE001 - transaction boundary normalizes provider errors
            if selection is not None and call_started is not None:
                failed_record = UsageRecord(
                    provider=str(selection.get("provider", "")),
                    model=str(selection.get("model", "")),
                    latency_ms=(time.monotonic() - call_started) * 1000.0,
                    estimated_cost_usd=None,
                    success=False,
                    streamed=config.streaming,
                    request_ref=input_data.request_ref,
                    error_type=type(exc).__name__,
                )
                try:
                    await _record_usage(input_data, failed_record)
                    step(
                        "usage_recorded",
                        provider=failed_record.provider,
                        model=failed_record.model,
                        total_tokens=0,
                        success=False,
                    )
                except Exception:
                    # A usage sink failure is reported by the transaction
                    # boundary; it must not mask the original provider error.
                    pass
            attempts.append({"success": False, "error_type": type(exc).__name__})
            step("provider_failure", error_type=type(exc).__name__, attempt=attempt_no + 1)
            # A failed call may not have produced a selection, so it is only
            # recorded when the selection was available in this iteration.

        decision = await _call(
            input_data.fallback_decision,
            attempts,
            policy=config.fallback_policy,
            max_attempts=config.max_attempts,
        )
        if not isinstance(decision, Mapping) or not bool(decision.get("retry")):
            break
        step("provider_fallback", attempt=attempt_no + 1)

    return build_result(
        status="failed",
        error={"type": "ProviderInferenceFailed", "message": "all provider attempts failed"},
        fingerprint=fingerprint,
        trail=trail,
        attempts=attempts,
    )


__all__ = [
    "ProviderInferenceConfig",
    "ProviderInferenceInput",
    "provider_inference_transaction",
]
