"""Tests for omodul.universe_selection.fixed_list."""
from __future__ import annotations

import pytest

from omodul.universe_selection import fixed_list, VALID_INSTRUMENT_TYPES


class TestFixedListBasic:
    def test_fixed_list_basic(self):
        result = fixed_list(
            symbols=["BTC-USDT", "ETH-USDT"],
            venue="OKX",
            instrument_type="perpetual",
        )
        assert result["instrument_ids"] == ["BTC-USDT.OKX", "ETH-USDT.OKX"]

    def test_fixed_list_venue_stored(self):
        result = fixed_list(["BTC-USDT"], "BINANCE", "spot")
        assert result["venue"] == "BINANCE"

    def test_fixed_list_instrument_type_stored(self):
        result = fixed_list(["BTC-USDT"], "OKX", "futures")
        assert result["instrument_type"] == "futures"

    def test_fixed_list_symbols_preserved(self):
        syms = ["BTC-USDT", "ETH-USDT", "SOL-USDT"]
        result = fixed_list(syms, "OKX", "spot")
        assert result["symbols"] == syms


class TestFixedListOutputKeys:
    def test_fixed_list_output_keys(self):
        result = fixed_list(["BTC-USDT"], "OKX", "spot")
        required = {"venue", "instrument_type", "symbols", "instrument_ids", "metadata"}
        assert required.issubset(set(result.keys()))

    def test_fixed_list_metadata_default_empty(self):
        result = fixed_list(["BTC-USDT"], "OKX", "spot")
        assert result["metadata"] == {}

    def test_fixed_list_metadata_custom(self):
        meta = {"sector": "crypto", "tier": "1"}
        result = fixed_list(["BTC-USDT"], "OKX", "spot", market_metadata=meta)
        assert result["metadata"] == meta


class TestFixedListValidation:
    def test_fixed_list_empty_raises(self):
        with pytest.raises(ValueError, match="non-empty"):
            fixed_list([], "OKX", "spot")

    def test_fixed_list_invalid_type_raises(self):
        with pytest.raises(ValueError, match="instrument_type"):
            fixed_list(["BTC-USDT"], "OKX", "crypto")

    def test_all_valid_instrument_types_accepted(self):
        for it in VALID_INSTRUMENT_TYPES:
            result = fixed_list(["BTC-USDT"], "OKX", it)
            assert result["instrument_type"] == it

    def test_instrument_id_format(self):
        result = fixed_list(["BTC-USDT", "ETH-USDT"], "OKX", "spot")
        for iid in result["instrument_ids"]:
            assert ".OKX" in iid
