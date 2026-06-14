"""Tests for omodul.login_provider."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from omodul.login_provider import Config, InputData, login_provider


# ---------------------------------------------------------------------------
# 1. Happy path — api_key mode with valid key
# ---------------------------------------------------------------------------
async def test_happy_path_api_key_returns_completed(tmp_path):
    inp = InputData(provider="anthropic", auth_mode="api_key", api_key="sk-valid")
    result = await login_provider(Config(), inp, tmp_path)
    assert result["status"] == "completed"
    assert result["provider"] == "anthropic"


# ---------------------------------------------------------------------------
# 2. Empty api_key → failed AuthError
# ---------------------------------------------------------------------------
async def test_empty_api_key_returns_failed(tmp_path):
    inp = InputData(provider="anthropic", auth_mode="api_key", api_key="")
    result = await login_provider(Config(), inp, tmp_path)
    assert result["status"] == "failed"
    assert result["error"]["type"] == "AuthError"


# ---------------------------------------------------------------------------
# 3. CancelledError re-raise
# ---------------------------------------------------------------------------
async def test_cancelled_error_propagates(tmp_path):
    async def slow_validator(**kw):
        await asyncio.sleep(100)

    inp = InputData(provider="openai", auth_mode="api_key", api_key="x", validator=slow_validator)
    task = asyncio.create_task(login_provider(Config(), inp, tmp_path))
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# ---------------------------------------------------------------------------
# 4. Exception → status="failed"
# ---------------------------------------------------------------------------
async def test_exception_returns_failed(tmp_path):
    def bad_validator(**kw):
        raise ConnectionError("network down")

    inp = InputData(provider="openai", auth_mode="api_key", api_key="x", validator=bad_validator)
    result = await login_provider(Config(), inp, tmp_path)
    assert result["status"] == "failed"
    assert result["error"]["type"] == "ConnectionError"


# ---------------------------------------------------------------------------
# 5. Validator returning False → AuthError
# ---------------------------------------------------------------------------
async def test_validator_false_returns_auth_error(tmp_path):
    validator = AsyncMock(return_value=False)
    inp = InputData(provider="anthropic", auth_mode="api_key", api_key="bad-key", validator=validator)
    result = await login_provider(Config(), inp, tmp_path)
    assert result["status"] == "failed"
    assert result["error"]["type"] == "AuthError"


# ---------------------------------------------------------------------------
# 6. Validator returning True → completed
# ---------------------------------------------------------------------------
async def test_validator_true_returns_completed(tmp_path):
    validator = AsyncMock(return_value=True)
    inp = InputData(provider="anthropic", auth_mode="api_key", api_key="sk-real", validator=validator)
    result = await login_provider(Config(), inp, tmp_path)
    assert result["status"] == "completed"


# ---------------------------------------------------------------------------
# 7. OAuth mode → completed (no key needed)
# ---------------------------------------------------------------------------
async def test_oauth_mode_returns_completed(tmp_path):
    inp = InputData(provider="google", auth_mode="oauth")
    result = await login_provider(Config(), inp, tmp_path)
    assert result["status"] == "completed"
    assert result["auth_mode"] == "oauth"


# ---------------------------------------------------------------------------
# 8. Unknown auth_mode → completed (recorded as unknown, not error)
# ---------------------------------------------------------------------------
async def test_unknown_auth_mode_returns_completed(tmp_path):
    inp = InputData(provider="custom", auth_mode="magic_link", api_key="x")
    result = await login_provider(Config(), inp, tmp_path)
    assert result["status"] == "completed"


# ---------------------------------------------------------------------------
# 9. on_step=None works
# ---------------------------------------------------------------------------
async def test_on_step_none_no_error(tmp_path):
    inp = InputData(provider="anthropic", auth_mode="api_key", api_key="sk-x")
    result = await login_provider(Config(), inp, tmp_path, on_step=None)
    assert result["status"] == "completed"


# ---------------------------------------------------------------------------
# 10. Required return keys present on success
# ---------------------------------------------------------------------------
async def test_return_keys_present_on_success(tmp_path):
    inp = InputData(provider="anthropic", auth_mode="api_key", api_key="sk-x")
    result = await login_provider(Config(), inp, tmp_path)
    for key in ("status", "error", "provider", "auth_mode"):
        assert key in result, f"missing key: {key}"


# ---------------------------------------------------------------------------
# 11. Concurrent calls — independent results
# ---------------------------------------------------------------------------
async def test_concurrent_calls_independent(tmp_path):
    async def run_one(provider):
        inp = InputData(provider=provider, auth_mode="api_key", api_key="sk-test")
        return await login_provider(Config(), inp, tmp_path)

    results = await asyncio.gather(run_one("anthropic"), run_one("openai"))
    assert results[0]["provider"] == "anthropic"
    assert results[1]["provider"] == "openai"
    assert all(r["status"] == "completed" for r in results)


# ---------------------------------------------------------------------------
# 12. Validator is called with key and provider
# ---------------------------------------------------------------------------
async def test_validator_called_with_correct_args(tmp_path):
    validator = AsyncMock(return_value=True)
    inp = InputData(provider="anthropic", auth_mode="api_key", api_key="sk-check", validator=validator)
    await login_provider(Config(), inp, tmp_path)
    validator.assert_called_once()
    kwargs = validator.call_args[1]
    assert kwargs["key"] == "sk-check"
    assert kwargs["provider"] == "anthropic"


# ---------------------------------------------------------------------------
# 13. result provider matches input provider
# ---------------------------------------------------------------------------
async def test_result_provider_matches_input(tmp_path):
    inp = InputData(provider="cohere", auth_mode="api_key", api_key="key123")
    result = await login_provider(Config(), inp, tmp_path)
    assert result["provider"] == "cohere"
