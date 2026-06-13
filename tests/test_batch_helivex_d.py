"""Batch D tests: 6 new omodul elements. All LLM calls mocked."""
from __future__ import annotations

import asyncio
import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ─────────────────────────── ohlcv_backfill ────────────────────────────


class TestOhlcvBackfill:
    def _config(self, **kw):
        from omodul.ohlcv_backfill import OhlcvBackfillConfig
        return OhlcvBackfillConfig(symbol="BTC-USDT-SWAP", **kw)

    def _fake_bars(self, n=3):
        return [
            {"ts": 1700000000000 + i * 3600000,
             "open": 42000.0, "high": 43000.0, "low": 41000.0,
             "close": 42500.0, "vol": 100.0, "vol_ccy": None}
            for i in range(n)
        ]

    @pytest.mark.asyncio
    async def test_returns_bars_fetched(self):
        from omodul.ohlcv_backfill import ohlcv_backfill
        with patch("oprim.ohlcv_fetch.ohlcv_fetch", new_callable=AsyncMock) as m:
            m.return_value = self._fake_bars(5)
            result = await ohlcv_backfill(config=self._config())
        assert result["bars_fetched"] == 5

    @pytest.mark.asyncio
    async def test_no_pool_skips_write(self):
        from omodul.ohlcv_backfill import ohlcv_backfill
        with patch("oprim.ohlcv_fetch.ohlcv_fetch", new_callable=AsyncMock) as m:
            m.return_value = self._fake_bars(3)
            result = await ohlcv_backfill(config=self._config(), pool=None)
        assert result["bars_written"] == 0

    @pytest.mark.asyncio
    async def test_with_pool_writes_all_bars(self):
        from omodul.ohlcv_backfill import ohlcv_backfill
        mock_pool = MagicMock()
        with patch("oprim.ohlcv_fetch.ohlcv_fetch", new_callable=AsyncMock) as m, \
             patch("obase.persistence.crud.write_one", new_callable=AsyncMock) as w:
            m.return_value = self._fake_bars(4)
            w.return_value = 1
            result = await ohlcv_backfill(config=self._config(), pool=mock_pool)
        assert result["bars_written"] == 4

    @pytest.mark.asyncio
    async def test_write_one_uses_conflict_columns(self):
        from omodul.ohlcv_backfill import ohlcv_backfill
        mock_pool = MagicMock()
        calls = []
        async def fake_write(pool, *, table, data, conflict_on=None):
            calls.append(conflict_on)
            return 1
        with patch("oprim.ohlcv_fetch.ohlcv_fetch", new_callable=AsyncMock) as m, \
             patch("obase.persistence.crud.write_one", side_effect=fake_write):
            m.return_value = self._fake_bars(2)
            await ohlcv_backfill(config=self._config(), pool=mock_pool)
        for c in calls:
            assert sorted(c) == sorted(["instrument", "bar", "ts"])

    @pytest.mark.asyncio
    async def test_enabled_pillars_decision_trail(self):
        from omodul.ohlcv_backfill import OhlcvBackfillConfig
        assert "decision_trail" in OhlcvBackfillConfig._enabled_pillars

    @pytest.mark.asyncio
    async def test_result_has_symbol(self):
        from omodul.ohlcv_backfill import ohlcv_backfill
        with patch("oprim.ohlcv_fetch.ohlcv_fetch", new_callable=AsyncMock) as m:
            m.return_value = []
            result = await ohlcv_backfill(config=self._config())
        assert result["symbol"] == "BTC-USDT-SWAP"

    @pytest.mark.asyncio
    async def test_result_has_interval(self):
        from omodul.ohlcv_backfill import ohlcv_backfill
        with patch("oprim.ohlcv_fetch.ohlcv_fetch", new_callable=AsyncMock) as m:
            m.return_value = []
            result = await ohlcv_backfill(config=self._config(interval="4H"))
        assert result["interval"] == "4H"

    @pytest.mark.asyncio
    async def test_empty_fetch_returns_zero(self):
        from omodul.ohlcv_backfill import ohlcv_backfill
        with patch("oprim.ohlcv_fetch.ohlcv_fetch", new_callable=AsyncMock) as m:
            m.return_value = []
            result = await ohlcv_backfill(config=self._config())
        assert result["bars_fetched"] == 0

    @pytest.mark.asyncio
    async def test_status_ok(self):
        from omodul.ohlcv_backfill import ohlcv_backfill
        with patch("oprim.ohlcv_fetch.ohlcv_fetch", new_callable=AsyncMock) as m:
            m.return_value = self._fake_bars(2)
            result = await ohlcv_backfill(config=self._config())
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_config_default_interval(self):
        from omodul.ohlcv_backfill import OhlcvBackfillConfig
        cfg = OhlcvBackfillConfig(symbol="ETH-USDT-SWAP")
        assert cfg.interval == "1H"


# ─────────────────────────── audit_record ────────────────────────────


class TestAuditRecord:
    def _keygen(self):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        key = Ed25519PrivateKey.generate()
        priv_b64 = base64.b64encode(key.private_bytes_raw()).decode()
        pub_b64 = base64.b64encode(key.public_key().public_bytes_raw()).decode()
        return priv_b64, pub_b64

    def _config(self, priv_b64=""):
        from omodul.audit_record import AuditRecordConfig
        return AuditRecordConfig(event_type="trade_executed",
                                 actor_id="user_001", private_key_b64=priv_b64)

    def test_returns_fingerprint_hex(self):
        from omodul.audit_record import audit_record
        result = audit_record({"amount": 100}, config=self._config())
        assert "fingerprint_hex" in result
        assert len(result["fingerprint_hex"]) == 64

    def test_fingerprint_excludes_tier(self):
        from omodul.audit_record import audit_record, AuditRecordConfig
        body = {"amount": 100}
        r_a = audit_record(body, config=AuditRecordConfig(event_type="x", tier="standard"))
        r_b = audit_record(body, config=AuditRecordConfig(event_type="x", tier="premium"))
        assert r_a["fingerprint_hex"] == r_b["fingerprint_hex"]

    def test_fingerprint_changes_with_body(self):
        from omodul.audit_record import audit_record
        r1 = audit_record({"amount": 100}, config=self._config())
        r2 = audit_record({"amount": 200}, config=self._config())
        assert r1["fingerprint_hex"] != r2["fingerprint_hex"]

    def test_signature_produced_with_key(self):
        from omodul.audit_record import audit_record
        priv, _ = self._keygen()
        result = audit_record({"x": 1}, config=self._config(priv))
        assert result["sig_b64"] != ""

    def test_no_key_produces_empty_sig(self):
        from omodul.audit_record import audit_record
        result = audit_record({"x": 1}, config=self._config(""))
        assert result["sig_b64"] == ""

    def test_verify_valid_record(self):
        from omodul.audit_record import audit_record, audit_verify
        priv, pub = self._keygen()
        result = audit_record({"data": "hello"}, config=self._config(priv))
        v = audit_verify(result, public_key_b64=pub)
        assert v["valid"] is True
        assert v["fingerprint_match"] is True
        assert v["sig_valid"] is True

    def test_verify_tampered_body_fails(self):
        from omodul.audit_record import audit_record, audit_verify
        priv, pub = self._keygen()
        result = audit_record({"data": "original"}, config=self._config(priv))
        tampered = dict(result)
        tampered["body"] = {"data": "tampered"}
        v = audit_verify(tampered, public_key_b64=pub)
        assert v["fingerprint_match"] is False
        assert v["valid"] is False

    def test_store_persists_record(self):
        from omodul.audit_record import audit_record
        store = {}
        audit_record({"k": "v"}, config=self._config(), store=store)
        assert len(store) == 1

    def test_enabled_pillars(self):
        from omodul.audit_record import AuditRecordConfig
        assert "decision_trail" in AuditRecordConfig._enabled_pillars

    def test_canonical_json_path_consistent(self):
        from omodul.audit_record import audit_record, audit_verify
        body = {"z": 3, "a": 1, "m": 2}
        result = audit_record(body, config=self._config())
        v = audit_verify(result)
        assert v["fingerprint_match"] is True

    def test_record_id_in_result(self):
        from omodul.audit_record import audit_record
        result = audit_record({"x": 1}, config=self._config())
        assert "record_id" in result


# ─────────────────────────── funding_arb ────────────────────────────


class TestFundingArb:
    def _config(self, **kw):
        from omodul.funding_arb import FundingArbConfig
        return FundingArbConfig(symbol="BTC-USDT-SWAP", **kw)

    def _rates(self, rate=0.0003, n=10):
        return [{"ts": i, "funding_rate": rate, "realized_rate": rate,
                 "next_funding_time": i + 28800000} for i in range(n)]

    def test_returns_expected_keys(self):
        from omodul.funding_arb import funding_arb
        result = funding_arb(config=self._config(), funding_rates=self._rates())
        for k in ("mean_funding_rate", "impact_bps", "net_bps", "arb_viable", "rates_sampled"):
            assert k in result

    def test_positive_funding_high_adv_viable(self):
        from omodul.funding_arb import funding_arb
        cfg = self._config(adv=100_000_000.0, notional=10_000.0,
                           impact_params={"alpha": 1.0, "beta": 5.0, "gamma": 0.5})
        result = funding_arb(config=cfg, funding_rates=self._rates(rate=0.001))
        assert result["arb_viable"] is True

    def test_zero_funding_not_viable(self):
        from omodul.funding_arb import funding_arb
        result = funding_arb(config=self._config(), funding_rates=self._rates(rate=0.0))
        assert result["arb_viable"] is False

    def test_rates_sampled_count(self):
        from omodul.funding_arb import funding_arb
        result = funding_arb(config=self._config(), funding_rates=self._rates(n=7))
        assert result["rates_sampled"] == 7

    def test_empty_rates_not_viable(self):
        from omodul.funding_arb import funding_arb
        result = funding_arb(config=self._config(), funding_rates=[])
        assert result["arb_viable"] is False

    def test_enabled_pillars(self):
        from omodul.funding_arb import FundingArbConfig
        assert "cost" in FundingArbConfig._enabled_pillars
        assert "decision_trail" in FundingArbConfig._enabled_pillars

    def test_mean_funding_rate_correct(self):
        from omodul.funding_arb import funding_arb
        result = funding_arb(config=self._config(), funding_rates=self._rates(rate=0.0005, n=4))
        assert result["mean_funding_rate"] == pytest.approx(0.0005)

    def test_impact_bps_positive(self):
        from omodul.funding_arb import funding_arb
        result = funding_arb(config=self._config(), funding_rates=self._rates())
        assert result["impact_bps"] > 0

    def test_pre_fetched_rates_skip_network(self):
        from omodul.funding_arb import funding_arb
        with patch("oprim.funding_rate_fetch.funding_rate_fetch") as m:
            funding_arb(config=self._config(), funding_rates=self._rates())
            m.assert_not_called()

    def test_annual_funding_bps_correct(self):
        from omodul.funding_arb import funding_arb
        result = funding_arb(config=self._config(), funding_rates=self._rates(rate=0.0003))
        expected = 0.0003 * 3 * 365 * 10_000
        assert result["annual_funding_bps"] == pytest.approx(expected, rel=1e-3)


# ─────────────────────────── stat_arb ────────────────────────────


class TestStatArb:
    def _coint_series(self, n=80):
        import numpy as np
        rng = np.random.default_rng(42)
        x = np.cumsum(rng.normal(0, 1, n))
        y = 2.0 * x + rng.normal(0, 0.05, n)
        return x.tolist(), y.tolist()

    def _config(self, **kw):
        from omodul.stat_arb import StatArbConfig
        return StatArbConfig(symbol_a="BTC", symbol_b="ETH", **kw)

    def test_returns_expected_keys(self):
        from omodul.stat_arb import stat_arb
        a, b = self._coint_series()
        result = stat_arb(a, b, config=self._config())
        for k in ("signal", "zscore", "cointegrated", "impact_bps_a",
                  "impact_bps_b", "total_impact_bps", "arb_viable"):
            assert k in result

    def test_signal_valid_value(self):
        from omodul.stat_arb import stat_arb
        a, b = self._coint_series()
        result = stat_arb(a, b, config=self._config())
        assert result["signal"] in ("long_a_short_b", "short_a_long_b", "close", "flat")

    def test_impact_bps_positive(self):
        from omodul.stat_arb import stat_arb
        a, b = self._coint_series()
        result = stat_arb(a, b, config=self._config())
        assert result["impact_bps_a"] > 0
        assert result["impact_bps_b"] > 0

    def test_cointegrated_for_coint_series(self):
        from omodul.stat_arb import stat_arb
        a, b = self._coint_series(100)
        result = stat_arb(a, b, config=self._config())
        assert result["cointegrated"] is True

    def test_enabled_pillars(self):
        from omodul.stat_arb import StatArbConfig
        assert "cost" in StatArbConfig._enabled_pillars
        assert "decision_trail" in StatArbConfig._enabled_pillars

    def test_hedge_ratio_in_result(self):
        from omodul.stat_arb import stat_arb
        a, b = self._coint_series()
        result = stat_arb(a, b, config=self._config())
        assert "hedge_ratio" in result
        assert result["hedge_ratio"] > 0

    def test_total_impact_is_sum_times_two(self):
        from omodul.stat_arb import stat_arb
        a, b = self._coint_series()
        result = stat_arb(a, b, config=self._config())
        assert result["total_impact_bps"] == pytest.approx(
            (result["impact_bps_a"] + result["impact_bps_b"]) * 2, rel=1e-3)

    def test_arb_viable_bool(self):
        from omodul.stat_arb import stat_arb
        a, b = self._coint_series()
        result = stat_arb(a, b, config=self._config())
        assert isinstance(result["arb_viable"], bool)

    def test_zscore_is_float(self):
        from omodul.stat_arb import stat_arb
        a, b = self._coint_series()
        result = stat_arb(a, b, config=self._config())
        assert isinstance(result["zscore"], float)

    def test_status_ok(self):
        from omodul.stat_arb import stat_arb
        a, b = self._coint_series()
        result = stat_arb(a, b, config=self._config())
        assert result["status"] == "ok"


# ─────────────────────────── backtest_gate ────────────────────────────


class TestBacktestGate:
    def _config(self, **kw):
        from omodul.backtest_gate import BacktestGateConfig
        return BacktestGateConfig(strategy_name="test_strat", n_splits=4, **kw)

    def _good_strategy(self, train, test):
        return {"sharpe": 2.5, "returns": [0.01] * 20}

    def _bad_strategy(self, train, test):
        return {"sharpe": -0.5, "returns": [-0.005] * 20}

    def test_returns_expected_keys(self):
        from omodul.backtest_gate import backtest_gate
        result = backtest_gate(self._good_strategy, list(range(40)), config=self._config())
        for k in ("gate_status", "deflated_sharpe", "pbo", "mean_oos_sharpe",
                  "fail_reasons", "walk_forward_result"):
            assert k in result

    def test_negative_sharpe_fails(self):
        from omodul.backtest_gate import backtest_gate
        result = backtest_gate(self._bad_strategy, list(range(40)), config=self._config())
        assert result["gate_status"] == "failed"

    def test_gate_status_is_string(self):
        from omodul.backtest_gate import backtest_gate
        result = backtest_gate(self._good_strategy, list(range(40)), config=self._config())
        assert result["gate_status"] in ("passed", "failed")

    def test_deflated_sharpe_zero_triggers_failure(self):
        from omodul.backtest_gate import backtest_gate
        result = backtest_gate(self._bad_strategy, list(range(40)), config=self._config())
        if result["deflated_sharpe"] <= 0:
            assert result["gate_status"] == "failed"

    def test_pbo_above_threshold_triggers_failure(self):
        from omodul.backtest_gate import backtest_gate
        result = backtest_gate(self._good_strategy, list(range(40)), config=self._config())
        if result["pbo"] > 0.5:
            assert result["gate_status"] == "failed"

    def test_fail_reasons_list(self):
        from omodul.backtest_gate import backtest_gate
        result = backtest_gate(self._bad_strategy, list(range(40)), config=self._config())
        assert isinstance(result["fail_reasons"], list)
        if result["gate_status"] == "failed":
            assert len(result["fail_reasons"]) > 0

    def test_pbo_in_unit_interval(self):
        from omodul.backtest_gate import backtest_gate
        result = backtest_gate(self._good_strategy, list(range(40)), config=self._config())
        assert 0.0 <= result["pbo"] <= 1.0

    def test_enabled_pillars(self):
        from omodul.backtest_gate import BacktestGateConfig
        assert "decision_trail" in BacktestGateConfig._enabled_pillars
        assert "report" in BacktestGateConfig._enabled_pillars

    def test_walk_forward_result_has_folds(self):
        from omodul.backtest_gate import backtest_gate
        result = backtest_gate(self._good_strategy, list(range(40)), config=self._config())
        assert len(result["walk_forward_result"]["fold_results"]) == 4

    def test_report_in_result(self):
        from omodul.backtest_gate import backtest_gate
        result = backtest_gate(self._good_strategy, list(range(40)), config=self._config())
        assert "report" in result
        assert "Strategy:" in result["report"]


# ─────────────────────────── llm_alpha_mine ────────────────────────────


class TestLlmAlphaMine:
    def _config(self, **kw):
        from omodul.llm_alpha_mine import LlmAlphaMineConfig
        return LlmAlphaMineConfig(
            market_context="BTC trending up",
            factor_hypothesis="momentum 12-1",
            n_splits=3, **kw,
        )

    def _caller(self, consensus="bullish", confidence=0.7):
        async def caller(messages, *, system="", max_tokens=512):
            text = f"Analysis.\nVERDICT: {consensus} CONFIDENCE: {confidence}"
            return {"content": [{"type": "text", "text": text}]}
        return caller

    def _strategy(self, train, test):
        return {"sharpe": 0.8}

    def test_returns_expected_keys(self):
        from omodul.llm_alpha_mine import llm_alpha_mine
        result = llm_alpha_mine(list(range(30)), config=self._config(),
                                llm_caller=self._caller(), strategy_fn=self._strategy)
        for k in ("fingerprint", "debate", "gate", "gate_status", "consensus"):
            assert k in result

    def test_fingerprint_stable_across_llm_outputs(self):
        from omodul.llm_alpha_mine import llm_alpha_mine
        data = list(range(30))
        r1 = llm_alpha_mine(data, config=self._config(),
                            llm_caller=self._caller("bullish", 0.9),
                            strategy_fn=self._strategy)
        r2 = llm_alpha_mine(data, config=self._config(),
                            llm_caller=self._caller("bearish", 0.3),
                            strategy_fn=self._strategy)
        assert r1["fingerprint"] == r2["fingerprint"]

    def test_fingerprint_changes_with_context(self):
        from omodul.llm_alpha_mine import llm_alpha_mine, LlmAlphaMineConfig
        data = list(range(30))
        cfg1 = LlmAlphaMineConfig(market_context="bull", factor_hypothesis="mom", n_splits=3)
        cfg2 = LlmAlphaMineConfig(market_context="bear", factor_hypothesis="mom", n_splits=3)
        r1 = llm_alpha_mine(data, config=cfg1, llm_caller=self._caller(),
                            strategy_fn=self._strategy)
        r2 = llm_alpha_mine(data, config=cfg2, llm_caller=self._caller(),
                            strategy_fn=self._strategy)
        assert r1["fingerprint"] != r2["fingerprint"]

    def test_llm_called_three_times(self):
        from omodul.llm_alpha_mine import llm_alpha_mine
        calls = []
        async def counting_caller(messages, *, system="", max_tokens=512):
            calls.append(1)
            return {"content": [{"type": "text", "text": "neutral"}]}
        llm_alpha_mine(list(range(30)), config=self._config(),
                       llm_caller=counting_caller, strategy_fn=self._strategy)
        assert len(calls) == 3

    def test_gate_status_in_result(self):
        from omodul.llm_alpha_mine import llm_alpha_mine
        result = llm_alpha_mine(list(range(30)), config=self._config(),
                                llm_caller=self._caller(), strategy_fn=self._strategy)
        assert result["gate_status"] in ("passed", "failed")

    def test_consensus_in_result(self):
        from omodul.llm_alpha_mine import llm_alpha_mine
        result = llm_alpha_mine(list(range(30)), config=self._config(),
                                llm_caller=self._caller("bearish"),
                                strategy_fn=self._strategy)
        assert result["consensus"] == "bearish"

    def test_debate_has_expected_keys(self):
        from omodul.llm_alpha_mine import llm_alpha_mine
        result = llm_alpha_mine(list(range(30)), config=self._config(),
                                llm_caller=self._caller(), strategy_fn=self._strategy)
        for k in ("consensus", "confidence", "bull_argument", "bear_argument", "verdict"):
            assert k in result["debate"]

    def test_gate_has_gate_status(self):
        from omodul.llm_alpha_mine import llm_alpha_mine
        result = llm_alpha_mine(list(range(30)), config=self._config(),
                                llm_caller=self._caller(), strategy_fn=self._strategy)
        assert "gate_status" in result["gate"]

    def test_enabled_pillars(self):
        from omodul.llm_alpha_mine import LlmAlphaMineConfig
        for p in ("cost", "decision_trail", "fingerprint"):
            assert p in LlmAlphaMineConfig._enabled_pillars

    def test_placeholder_strategy_when_none(self):
        from omodul.llm_alpha_mine import llm_alpha_mine
        result = llm_alpha_mine(list(range(30)), config=self._config(),
                                llm_caller=self._caller("bullish", 0.8),
                                strategy_fn=None)
        assert result["gate_status"] in ("passed", "failed")

    def test_factor_hypothesis_in_debate_prompt(self):
        from omodul.llm_alpha_mine import llm_alpha_mine
        seen = []
        async def capturing(messages, *, system="", max_tokens=512):
            seen.extend(messages)
            return {"content": [{"type": "text", "text": "neutral"}]}
        llm_alpha_mine(list(range(30)), config=self._config(),
                       llm_caller=capturing, strategy_fn=self._strategy)
        combined = " ".join(m.get("content", "") for m in seen)
        assert "momentum 12-1" in combined
