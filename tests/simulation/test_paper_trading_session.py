"""Tests for paper_trading_session (Sprint 0)."""

from __future__ import annotations

from datetime import datetime, date

import pytest

from omodul.simulation.paper_trading_session import paper_trading_session

STABILITY = "experimental"


def _rules(limit_pct=0.10):
    return {
        "daily_limit": {"get_limit_pct": lambda sym: limit_pct},
        "t_plus_n": 1,
        "commission": {"rate": 0.001, "min_fee": 5.0},
        "stamp_tax": {"rate": 0.001, "direction": "sell"},
    }


def _account(cash=100_000.0):
    return {"cash": cash, "positions": [], "history": []}


def _ohlcv(symbol="AAPL", open_=150.0, close=152.0, high=155.0, low=149.0, prev_close=150.0):
    return {symbol: {"open": open_, "close": close, "high": high, "low": low, "prev_close": prev_close}}


class TestPaperTradingSession:
    def test_basic_buy_executes(self):
        orders = [{"order_id": "o1", "symbol": "AAPL", "side": "buy",
                   "quantity": 10, "order_type": "market"}]
        result = paper_trading_session(
            _account(), orders, _ohlcv(), _rules(), datetime(2026, 1, 5, 9, 30)
        )
        assert len(result["executed_trades"]) == 1
        assert len(result["rejected_orders"]) == 0
        assert result["new_account_state"]["cash"] < 100_000.0
        assert any(p["symbol"] == "AAPL" for p in result["new_account_state"]["positions"])

    def test_sell_after_buy_updates_pnl(self):
        ts = datetime(2026, 1, 5, 9, 30)
        acct = _account()
        # buy first
        buy = [{"order_id": "b1", "symbol": "AAPL", "side": "buy",
                "quantity": 10, "order_type": "market"}]
        r1 = paper_trading_session(acct, buy, _ohlcv(open_=100.0, close=100.0, prev_close=100.0), _rules(), ts)
        acct2 = r1["new_account_state"]
        # sell next session (modify entry_date to allow T+1)
        acct2["positions"][0]["entry_date"] = date(2026, 1, 4)
        sell = [{"order_id": "s1", "symbol": "AAPL", "side": "sell",
                 "quantity": 10, "order_type": "market"}]
        # prev_close close to open to avoid limit-down trigger
        r2 = paper_trading_session(
            acct2, sell,
            _ohlcv(open_=110.0, close=110.0, high=112.0, low=109.0, prev_close=109.0),
            _rules(), datetime(2026, 1, 6, 9, 30)
        )
        assert len(r2["executed_trades"]) == 1
        assert r2["pnl_delta"] > 0

    def test_no_market_data_rejects(self):
        orders = [{"order_id": "o1", "symbol": "UNKNOWN", "side": "buy", "quantity": 10}]
        result = paper_trading_session(_account(), orders, {}, _rules(), datetime(2026, 1, 5))
        assert len(result["rejected_orders"]) == 1
        assert result["rejected_orders"][0]["reason"] == "no_market_data"

    def test_insufficient_cash_rejects(self):
        orders = [{"order_id": "o1", "symbol": "AAPL", "side": "buy",
                   "quantity": 10000, "order_type": "market"}]
        result = paper_trading_session(
            _account(cash=1.0), orders, _ohlcv(), _rules(), datetime(2026, 1, 5)
        )
        assert any(o["reason"] == "insufficient_cash" for o in result["rejected_orders"])

    def test_sell_without_position_rejects(self):
        orders = [{"order_id": "o1", "symbol": "AAPL", "side": "sell", "quantity": 10}]
        result = paper_trading_session(_account(), orders, _ohlcv(), _rules(), datetime(2026, 1, 5))
        assert any(o["reason"] == "no_position" for o in result["rejected_orders"])

    def test_t_plus_1_blocked(self):
        ts = datetime(2026, 1, 5, 9, 30)
        acct = _account()
        # manually add position with today's entry_date
        acct["positions"] = [{"symbol": "AAPL", "qty": 10, "entry_price": 150.0, "entry_date": date(2026, 1, 5)}]
        sell = [{"order_id": "s1", "symbol": "AAPL", "side": "sell", "quantity": 10}]
        result = paper_trading_session(acct, sell, _ohlcv(), _rules(), ts)
        assert any("t_plus_1_blocked" in o["reason"] for o in result["rejected_orders"])

    def test_limit_order_buy_fills_when_hit(self):
        ohlcv = _ohlcv(open_=150.0, low=148.0)
        orders = [{"order_id": "o1", "symbol": "AAPL", "side": "buy",
                   "quantity": 5, "order_type": "limit", "price": 149.0}]
        result = paper_trading_session(_account(), orders, ohlcv, _rules(), datetime(2026, 1, 5))
        assert len(result["executed_trades"]) == 1

    def test_limit_order_buy_not_hit_rejects(self):
        ohlcv = _ohlcv(open_=152.0, low=151.0)
        orders = [{"order_id": "o1", "symbol": "AAPL", "side": "buy",
                   "quantity": 5, "order_type": "limit", "price": 148.0}]
        result = paper_trading_session(_account(), orders, ohlcv, _rules(), datetime(2026, 1, 5))
        assert any(o["reason"] == "limit_not_hit" for o in result["rejected_orders"])

    def test_limit_up_blocks_buy(self):
        # close 10.1% above prev_close → limit up
        ohlcv = {"AAPL": {"open": 110.0, "close": 111.0, "high": 111.0, "low": 109.0, "prev_close": 100.0}}
        orders = [{"order_id": "o1", "symbol": "AAPL", "side": "buy", "quantity": 10}]
        result = paper_trading_session(_account(), orders, ohlcv, _rules(0.10), datetime(2026, 1, 5))
        assert any(o["reason"] == "limit_up_block_buy" for o in result["rejected_orders"])

    def test_limit_down_blocks_sell(self):
        ts = datetime(2026, 1, 5)
        acct = _account()
        acct["positions"] = [{"symbol": "AAPL", "qty": 10, "entry_price": 100.0,
                               "entry_date": date(2026, 1, 4)}]
        # close 10.1% below prev_close → limit down
        ohlcv = {"AAPL": {"open": 89.0, "close": 89.0, "high": 90.0, "low": 88.0, "prev_close": 100.0}}
        sell = [{"order_id": "s1", "symbol": "AAPL", "side": "sell", "quantity": 10}]
        result = paper_trading_session(acct, sell, ohlcv, _rules(0.10), ts)
        assert any(o["reason"] == "limit_down_block_sell" for o in result["rejected_orders"])

    def test_multiple_orders_mixed_results(self):
        orders = [
            {"order_id": "o1", "symbol": "AAPL", "side": "buy", "quantity": 5},
            {"order_id": "o2", "symbol": "MISSING", "side": "buy", "quantity": 5},
        ]
        result = paper_trading_session(_account(), orders, _ohlcv(), _rules(), datetime(2026, 1, 5))
        assert len(result["executed_trades"]) == 1
        assert len(result["rejected_orders"]) == 1

    def test_position_averaging(self):
        ts = datetime(2026, 1, 5)
        acct = _account()
        acct["positions"] = [{"symbol": "AAPL", "qty": 10, "entry_price": 100.0,
                               "entry_date": date(2026, 1, 4)}]
        buy2 = [{"order_id": "o1", "symbol": "AAPL", "side": "buy", "quantity": 10, "order_type": "market"}]
        result = paper_trading_session(acct, buy2, _ohlcv(open_=110.0), _rules(), ts)
        pos = next(p for p in result["new_account_state"]["positions"] if p["symbol"] == "AAPL")
        assert pos["qty"] == 20
        assert 100.0 < pos["entry_price"] < 110.0

    def test_limit_order_sell_fills_when_hit(self):
        """Cover line 125: limit sell fills when lp <= high_price."""
        ts = datetime(2026, 1, 5)
        acct = _account()
        acct["positions"] = [{"symbol": "AAPL", "qty": 10, "entry_price": 100.0,
                               "entry_date": date(2026, 1, 4)}]
        ohlcv = _ohlcv(open_=110.0, high=115.0, low=108.0, close=111.0, prev_close=109.0)
        sell = [{"order_id": "s1", "symbol": "AAPL", "side": "sell",
                 "quantity": 10, "order_type": "limit", "price": 112.0}]
        result = paper_trading_session(acct, sell, ohlcv, _rules(), ts)
        assert len(result["executed_trades"]) == 1

    def test_unknown_order_type_uses_open(self):
        """Cover line 130: unknown order_type falls back to open_price."""
        orders = [{"order_id": "o1", "symbol": "AAPL", "side": "buy",
                   "quantity": 5, "order_type": "vwap"}]
        result = paper_trading_session(_account(), orders, _ohlcv(open_=100.0), _rules(), datetime(2026, 1, 5))
        assert len(result["executed_trades"]) == 1
        assert result["executed_trades"][0]["fill_price"] == pytest.approx(100.0)

    def test_partial_sell_updates_position(self):
        """Cover line 181: partial sell leaves remaining qty in position."""
        ts = datetime(2026, 1, 5)
        acct = _account()
        acct["positions"] = [{"symbol": "AAPL", "qty": 20, "entry_price": 100.0,
                               "entry_date": date(2026, 1, 4)}]
        sell = [{"order_id": "s1", "symbol": "AAPL", "side": "sell",
                 "quantity": 10, "order_type": "market"}]
        result = paper_trading_session(acct, sell, _ohlcv(open_=100.0, prev_close=99.0), _rules(), ts)
        assert len(result["executed_trades"]) == 1
        positions = result["new_account_state"]["positions"]
        aapl = next((p for p in positions if p["symbol"] == "AAPL"), None)
        assert aapl is not None
        assert aapl["qty"] == 10.0

    @pytest.mark.academic_reference
    def test_academic_reference_paper_trading(self):
        """Paper trading simulation follows Lo & MacKinlay (1999) transaction cost model
        with stamp tax as per A-share market rules (SZSE/SSE regulations)."""
        orders = [{"order_id": "o1", "symbol": "AAPL", "side": "buy",
                   "quantity": 100, "order_type": "market"}]
        result = paper_trading_session(
            _account(), orders, _ohlcv(open_=100.0), _rules(), datetime(2026, 1, 5)
        )
        trade = result["executed_trades"][0]
        # Commission should apply
        assert trade["fees"] >= 5.0  # min_fee
        assert trade["fill_price"] == pytest.approx(100.0)
