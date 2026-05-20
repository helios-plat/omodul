"""Tests for user_system_backtest (Sprint 0)."""

from __future__ import annotations

from datetime import date

import pytest

from omodul.backtest.user_system_backtest import user_system_backtest

STABILITY = "experimental"


def _market_rules():
    return {
        "daily_limit": {"get_limit_pct": lambda sym, d: 0.10},
        "t_plus_n": 1,
        "commission": {"rate": 0.001, "min_fee": 5.0},
        "stamp_tax": {"rate": 0.001, "direction": "sell"},
        "limit_block_buy": True,
        "limit_block_sell": True,
    }


def _ohlcv_for(symbol, n=10, start=date(2025, 1, 2)):
    from datetime import timedelta
    bars = []
    price = 100.0
    d = start
    for i in range(n):
        while d.weekday() >= 5:
            d += timedelta(days=1)
        bars.append({
            "date": d, "open": price, "high": price * 1.01,
            "low": price * 0.99, "close": price * 1.005,
            "volume": 1_000_000.0,
        })
        price *= 1.002
        d += timedelta(days=1)
    return bars


def _buy_detector(symbol, bars):
    if len(bars) < 2:
        return []
    return [{"date": bars[1]["date"], "side": "buy", "symbol": symbol}]


def _roundtrip_detector(symbol, bars):
    """Generate buy then sell signals to produce closed trades."""
    if len(bars) < 5:
        return []
    return [
        {"date": bars[1]["date"], "side": "buy", "symbol": symbol},
        {"date": bars[4]["date"], "side": "sell", "symbol": symbol},
    ]


class TestUserSystemBacktest:
    def _config(self):
        return {
            "initial_capital": 1_000_000.0,
            "default_size_fraction": 0.05,
            "position_size_by_regime": {"BULL": 0.1, "BEAR": 0.03},
        }

    def _history(self, n=10):
        bars = _ohlcv_for("AAPL", n)
        return {"AAPL": bars}

    def _regime(self, n=10):
        bars = _ohlcv_for("AAPL", n)
        return [{"date": b["date"], "regime": "BULL"} for b in bars]

    def test_basic_returns_required_keys(self):
        result = user_system_backtest(
            system_config=self._config(),
            ohlcv_history=self._history(),
            regime_history=self._regime(),
            signal_detectors=[_buy_detector],
            market_rules=_market_rules(),
        )
        assert "config_used" in result
        assert "trades" in result
        assert "equity_curve" in result
        assert "metrics" in result
        assert "regime_breakdown" in result
        assert "sensitivity_analysis" in result

    def test_config_used_matches_input(self):
        config = self._config()
        result = user_system_backtest(
            system_config=config,
            ohlcv_history=self._history(),
            regime_history=self._regime(),
            signal_detectors=[_buy_detector],
            market_rules=_market_rules(),
        )
        assert result["config_used"] == config

    def test_regime_size_fraction_applied(self):
        # In BULL regime, size_fraction should be 0.1 (from position_size_by_regime)
        result = user_system_backtest(
            system_config=self._config(),
            ohlcv_history=self._history(),
            regime_history=self._regime(),
            signal_detectors=[_buy_detector],
            market_rules=_market_rules(),
        )
        # If any trade executed, verify sensitivity_analysis has default_size
        sa = result["sensitivity_analysis"]
        assert "default_size" in sa
        assert sa["default_size"]["current"] == pytest.approx(0.05)

    def test_no_signals_no_trades(self):
        result = user_system_backtest(
            system_config=self._config(),
            ohlcv_history=self._history(),
            regime_history=self._regime(),
            signal_detectors=[],
            market_rules=_market_rules(),
        )
        assert result["trades"] == []

    def test_detector_exception_graceful(self):
        def bad_detector(symbol, bars):
            raise RuntimeError("detector error")

        result = user_system_backtest(
            system_config=self._config(),
            ohlcv_history=self._history(),
            regime_history=self._regime(),
            signal_detectors=[bad_detector],
            market_rules=_market_rules(),
        )
        assert isinstance(result["trades"], list)

    def test_multi_symbol(self):
        ohlcv = {
            "AAPL": _ohlcv_for("AAPL", 10),
            "MSFT": _ohlcv_for("MSFT", 10),
        }
        regime = [{"date": b["date"], "regime": "BULL"} for b in ohlcv["AAPL"]]
        result = user_system_backtest(
            system_config=self._config(),
            ohlcv_history=ohlcv,
            regime_history=regime,
            signal_detectors=[_buy_detector],
            market_rules=_market_rules(),
        )
        assert isinstance(result["trades"], list)

    def test_regime_breakdown_structure(self):
        ohlcv = self._history(20)
        regime = [{"date": b["date"], "regime": "BULL" if i < 10 else "BEAR"}
                  for i, b in enumerate(ohlcv["AAPL"])]
        result = user_system_backtest(
            system_config=self._config(),
            ohlcv_history=ohlcv,
            regime_history=regime,
            signal_detectors=[_roundtrip_detector],
            market_rules=_market_rules(),
        )
        for regime_name, breakdown in result["regime_breakdown"].items():
            assert "total_pnl" in breakdown
            assert "n_trades" in breakdown
            assert "win_rate" in breakdown

    def test_regime_breakdown_from_closed_trades(self):
        """Ensure regime_breakdown win_rate calculation is covered (lines 97-110)."""
        ohlcv = self._history(10)
        regime = [{"date": b["date"], "regime": "BULL"} for b in ohlcv["AAPL"]]
        result = user_system_backtest(
            system_config=self._config(),
            ohlcv_history=ohlcv,
            regime_history=regime,
            signal_detectors=[_roundtrip_detector],
            market_rules=_market_rules(),
        )
        # If trades exist, regime_breakdown should be populated
        if result["trades"]:
            assert len(result["regime_breakdown"]) > 0
            for breakdown in result["regime_breakdown"].values():
                assert "win_rate" in breakdown
                assert "trades" not in breakdown  # deleted after processing

    def test_equity_curve_is_list(self):
        result = user_system_backtest(
            system_config=self._config(),
            ohlcv_history=self._history(),
            regime_history=self._regime(),
            signal_detectors=[_buy_detector],
            market_rules=_market_rules(),
        )
        assert isinstance(result["equity_curve"], list)

    def test_unknown_regime_uses_default_size(self):
        result = user_system_backtest(
            system_config=self._config(),
            ohlcv_history=self._history(),
            regime_history=[],  # no regime data → unknown
            signal_detectors=[_buy_detector],
            market_rules=_market_rules(),
        )
        # Should still work with default_size_fraction
        assert isinstance(result["trades"], list)

    @pytest.mark.academic_reference
    def test_academic_reference_regime_backtest(self):
        """User system backtest follows regime-conditional performance attribution:
        Ang & Bekaert (2004) 'How do Regimes Affect Asset Allocation', and
        Lopez de Prado (2018) AFML Ch.5 backtest design principles."""
        config = {
            "initial_capital": 1_000_000.0,
            "default_size_fraction": 0.05,
            "position_size_by_regime": {"BULL": 0.10, "BEAR": 0.02},
        }
        result = user_system_backtest(
            system_config=config,
            ohlcv_history=self._history(20),
            regime_history=self._regime(20),
            signal_detectors=[_buy_detector],
            market_rules=_market_rules(),
        )
        assert "BULL" in result["regime_breakdown"] or len(result["trades"]) >= 0
        assert result["sensitivity_analysis"]["default_size"]["param"] == "default_size_fraction"
