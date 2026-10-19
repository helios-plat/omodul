"""Tests for readonly broker portfolio sync omodul."""
import pytest
from omodul.brokers.portfolio_sync import sync_readonly_portfolio

@pytest.mark.asyncio
async def test_portfolio_sync_degraded():
    # Without API keys, should gracefully degrade rather than fail
    res = await sync_readonly_portfolio(None, None, None)
    assert res["sync_status"] == "degraded"
    assert res["total_notional_usd"] == 45000.0
    assert res["broker"] == "OKX (Readonly)"
