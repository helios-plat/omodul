"""Tests for omodul.sync_models_catalog."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from omodul.sync_models_catalog import Config, InputData, sync_models_catalog, compute_fingerprint_for


def _models(providers=("anthropic", "openai", "google", "meta")):
    return [{"name": f"{p}-model", "provider": p} for p in providers]


# ---------------------------------------------------------------------------
# 1. Happy path — fetcher returns models, filtered by curated_providers
# ---------------------------------------------------------------------------
async def test_happy_path_returns_completed(tmp_path):
    fetcher = AsyncMock(return_value=_models())
    inp = InputData(fetcher=fetcher)
    result = await sync_models_catalog(Config(), inp, tmp_path)
    assert result["status"] == "completed"


# ---------------------------------------------------------------------------
# 2. Models filtered to curated providers only
# ---------------------------------------------------------------------------
async def test_models_filtered_to_curated(tmp_path):
    fetcher = AsyncMock(return_value=_models(["anthropic", "openai", "google", "meta", "cohere"]))
    cfg = Config(curated_providers=["anthropic", "openai"])
    inp = InputData(fetcher=fetcher)
    result = await sync_models_catalog(cfg, inp, tmp_path)
    assert result["curated_count"] == 2
    providers = {m["provider"] for m in result["models"]}
    assert providers == {"anthropic", "openai"}


# ---------------------------------------------------------------------------
# 3. CancelledError re-raise
# ---------------------------------------------------------------------------
async def test_cancelled_error_propagates(tmp_path):
    async def slow_fetcher(**kw):
        await asyncio.sleep(100)

    inp = InputData(fetcher=slow_fetcher)
    task = asyncio.create_task(sync_models_catalog(Config(), inp, tmp_path))
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# ---------------------------------------------------------------------------
# 4. Exception → status="failed"
# ---------------------------------------------------------------------------
async def test_exception_returns_failed(tmp_path):
    def bad_fetcher(**kw):
        raise ConnectionError("timeout")

    inp = InputData(fetcher=bad_fetcher)
    result = await sync_models_catalog(Config(), inp, tmp_path)
    assert result["status"] == "failed"
    assert result["error"]["type"] == "ConnectionError"


# ---------------------------------------------------------------------------
# 5. No fetcher → empty models list
# ---------------------------------------------------------------------------
async def test_no_fetcher_returns_empty_models(tmp_path):
    inp = InputData()
    result = await sync_models_catalog(Config(), inp, tmp_path)
    assert result["status"] == "completed"
    assert result["models"] == []
    assert result["total_fetched"] == 0


# ---------------------------------------------------------------------------
# 6. compute_fingerprint_for is deterministic
# ---------------------------------------------------------------------------
def test_compute_fingerprint_for_deterministic():
    cfg = Config(catalog_version="v2", curated_providers=["anthropic", "openai"])
    inp = InputData()
    fp1 = compute_fingerprint_for(cfg, inp)
    fp2 = compute_fingerprint_for(cfg, inp)
    assert fp1 == fp2


# ---------------------------------------------------------------------------
# 7. compute_fingerprint_for changes with different catalog_version
# ---------------------------------------------------------------------------
def test_compute_fingerprint_for_changes_with_version():
    cfg1 = Config(catalog_version="v1")
    cfg2 = Config(catalog_version="v2")
    inp = InputData()
    assert compute_fingerprint_for(cfg1, inp) != compute_fingerprint_for(cfg2, inp)


# ---------------------------------------------------------------------------
# 8. compute_fingerprint_for changes with different curated_providers
# ---------------------------------------------------------------------------
def test_compute_fingerprint_for_changes_with_providers():
    cfg1 = Config(curated_providers=["anthropic"])
    cfg2 = Config(curated_providers=["openai"])
    inp = InputData()
    assert compute_fingerprint_for(cfg1, inp) != compute_fingerprint_for(cfg2, inp)


# ---------------------------------------------------------------------------
# 9. Provider order does not affect fingerprint (sorted internally)
# ---------------------------------------------------------------------------
def test_compute_fingerprint_provider_order_invariant():
    cfg1 = Config(curated_providers=["anthropic", "openai"])
    cfg2 = Config(curated_providers=["openai", "anthropic"])
    inp = InputData()
    assert compute_fingerprint_for(cfg1, inp) == compute_fingerprint_for(cfg2, inp)


# ---------------------------------------------------------------------------
# 10. on_step=None works
# ---------------------------------------------------------------------------
async def test_on_step_none_no_error(tmp_path):
    inp = InputData()
    result = await sync_models_catalog(Config(), inp, tmp_path, on_step=None)
    assert result["status"] == "completed"


# ---------------------------------------------------------------------------
# 11. Required return keys present
# ---------------------------------------------------------------------------
async def test_return_keys_present(tmp_path):
    inp = InputData()
    result = await sync_models_catalog(Config(), inp, tmp_path)
    for key in ("status", "error", "fingerprint", "total_fetched", "curated_count", "models"):
        assert key in result, f"missing key: {key}"


# ---------------------------------------------------------------------------
# 12. total_fetched matches all models from fetcher
# ---------------------------------------------------------------------------
async def test_total_fetched_count(tmp_path):
    all_models = _models(["anthropic", "openai", "meta", "mistral", "cohere"])
    fetcher = AsyncMock(return_value=all_models)
    inp = InputData(fetcher=fetcher)
    result = await sync_models_catalog(Config(), inp, tmp_path)
    assert result["total_fetched"] == 5


# ---------------------------------------------------------------------------
# 13. Concurrent calls succeed independently
# ---------------------------------------------------------------------------
async def test_concurrent_calls_independent(tmp_path):
    fetcher = AsyncMock(return_value=_models(["anthropic"]))

    async def run_one():
        inp = InputData(fetcher=fetcher)
        return await sync_models_catalog(Config(curated_providers=["anthropic"]), inp, tmp_path)

    results = await asyncio.gather(run_one(), run_one())
    assert all(r["status"] == "completed" for r in results)
    assert all(r["curated_count"] == 1 for r in results)
