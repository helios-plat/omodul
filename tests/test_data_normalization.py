"""Tests for omodul.data_normalization.okx_to_nautilus."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from omodul.data_normalization import okx_to_nautilus

REAL_DATA_DIR = Path(__file__).parent / "real_data"


def _load(filename: str) -> dict:
    path = REAL_DATA_DIR / filename
    if not path.exists():
        pytest.skip(f"Fixture not found: {path}")
    with open(path) as f:
        return json.load(f)


class TestOkxTickers:
    def test_okx_tickers_to_nautilus(self):
        payload = _load("okx_raw_tickers_sample.json")
        result = okx_to_nautilus(payload)
        assert result["event_type"] == "tick"
        assert result["instrument_id"] == "BTC-USDT.OKX"
        assert result["venue"] == "OKX"
        assert isinstance(result["timestamp_ns"], int)
        d = result["data"]
        assert "price" in d
        assert "bid" in d
        assert "ask" in d
        assert "bid_size" in d
        assert "ask_size" in d
        assert "volume_24h" in d

    def test_okx_tickers_price_float(self):
        payload = _load("okx_raw_tickers_sample.json")
        result = okx_to_nautilus(payload)
        assert result["data"]["price"] == pytest.approx(45000.5)
        assert result["data"]["bid"] == pytest.approx(44999.5)
        assert result["data"]["ask"] == pytest.approx(45001.0)


class TestOkxOrderbook:
    def test_okx_orderbook_to_nautilus(self):
        payload = _load("okx_raw_orderbook_sample.json")
        result = okx_to_nautilus(payload)
        assert result["event_type"] == "orderbook"
        assert result["instrument_id"] == "BTC-USDT.OKX"
        assert isinstance(result["timestamp_ns"], int)

    def test_okx_orderbook_bids_asks_structure(self):
        payload = _load("okx_raw_orderbook_sample.json")
        result = okx_to_nautilus(payload)
        d = result["data"]
        assert "bids" in d
        assert "asks" in d
        # Each entry should be [price, size] as floats
        assert isinstance(d["bids"][0], list)
        assert len(d["bids"][0]) == 2
        assert all(isinstance(v, float) for v in d["bids"][0])

    def test_okx_orderbook_multiple_levels(self):
        payload = _load("okx_raw_orderbook_sample.json")
        result = okx_to_nautilus(payload)
        assert len(result["data"]["bids"]) == 3
        assert len(result["data"]["asks"]) == 3


class TestOkxTrades:
    def test_okx_trades_to_nautilus(self):
        payload = _load("okx_raw_trades_sample.json")
        result = okx_to_nautilus(payload)
        assert result["event_type"] == "trade"
        assert result["instrument_id"] == "BTC-USDT.OKX"
        assert isinstance(result["timestamp_ns"], int)

    def test_okx_trades_data_fields(self):
        payload = _load("okx_raw_trades_sample.json")
        result = okx_to_nautilus(payload)
        d = result["data"]
        assert "price" in d
        assert "size" in d
        assert "side" in d
        assert "trade_id" in d
        assert d["side"] == "buy"
        assert d["price"] == pytest.approx(45000.5)


class TestOkxCandle:
    def test_okx_candle_to_nautilus(self):
        payload = _load("okx_raw_candle1m_sample.json")
        result = okx_to_nautilus(payload)
        assert result["event_type"] == "bar"
        assert result["instrument_id"] == "BTC-USDT.OKX"

    def test_okx_candle_data_fields(self):
        payload = _load("okx_raw_candle1m_sample.json")
        result = okx_to_nautilus(payload)
        d = result["data"]
        assert "open" in d
        assert "high" in d
        assert "low" in d
        assert "close" in d
        assert "volume" in d
        assert "bar_type" in d
        assert d["bar_type"] == "candle1m"

    def test_okx_candle5m_maps_to_bar(self):
        """candle5m should also produce event_type=bar."""
        payload = {
            "arg": {"channel": "candle5m", "instId": "ETH-USDT"},
            "data": [["1700000000000", "2000.0", "2010.0", "1990.0", "2005.0", "50.0", "100500.0"]],
        }
        result = okx_to_nautilus(payload)
        assert result["event_type"] == "bar"
        assert result["data"]["bar_type"] == "candle5m"


class TestOkxErrors:
    def test_okx_unknown_channel_raises(self):
        payload = {
            "arg": {"channel": "unknown_xyz", "instId": "BTC-USDT"},
            "data": [{}],
        }
        with pytest.raises(ValueError, match="unknown_xyz"):
            okx_to_nautilus(payload)

    def test_timestamp_conversion(self):
        """ts=1700000000000 (ms) → timestamp_ns = 1700000000000 * 1_000_000."""
        payload = _load("okx_raw_tickers_sample.json")
        result = okx_to_nautilus(payload)
        expected_ns = 1700000000000 * 1_000_000
        assert result["timestamp_ns"] == expected_ns
