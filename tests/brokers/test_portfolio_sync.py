"""Tests for readonly broker portfolio sync omodul."""

from typing import Any

import pytest

import omodul.brokers.portfolio_sync as portfolio_sync


@pytest.mark.asyncio
async def test_portfolio_sync_degraded_without_credentials_has_no_mock_values():
    result = await portfolio_sync.sync_readonly_portfolio(None, None, None)
    assert result["sync_status"] == "degraded"
    assert result["position_count"] is None
    assert result["total_notional_usd"] is None
    assert result["balance"] == []
    assert "missing_credentials" in result["degraded_reason"]
    assert result["broker"] == "OKX (Readonly)"


@pytest.mark.asyncio
async def test_portfolio_sync_summarizes_only_returned_readonly_data(monkeypatch):
    class StubReadonlyAdapter:
        def __init__(self, **_: Any) -> None:
            pass

        async def get_balance(self):
            return {"status": "success", "balance": [{"totalEq": "real-response-field"}]}

        async def get_positions(self):
            return {
                "status": "success",
                "positions": [{"notionalUsd": "12.5"}, {"notionalUsd": "7.25"}],
            }

    monkeypatch.setattr(portfolio_sync, "OKXReadonlyBrokerAdapter", StubReadonlyAdapter)
    result = await portfolio_sync.sync_readonly_portfolio("key", "secret", "passphrase")
    assert result["sync_status"] == "success"
    assert result["position_count"] == 2
    assert result["total_notional_usd"] == pytest.approx(19.75)
    assert result["balance"] == [{"totalEq": "real-response-field"}]


@pytest.mark.asyncio
async def test_portfolio_sync_does_not_estimate_missing_notional(monkeypatch):
    class StubReadonlyAdapter:
        def __init__(self, **_: Any) -> None:
            pass

        async def get_balance(self):
            return {"status": "success", "balance": []}

        async def get_positions(self):
            return {"status": "success", "positions": [{"instId": "BTC-USDT-SWAP"}]}

    monkeypatch.setattr(portfolio_sync, "OKXReadonlyBrokerAdapter", StubReadonlyAdapter)
    result = await portfolio_sync.sync_readonly_portfolio("key", "secret", "passphrase")
    assert result["sync_status"] == "success"
    assert result["position_count"] == 1
    assert result["total_notional_usd"] is None
