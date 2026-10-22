from pathlib import Path
import pytest
from omodul.tradingview_market_intelligence import TradingViewMarketConfig, TradingViewMarketInput, tradingview_market_intelligence

class StubTransport:
    def __init__(self, failed=False): self.failed = failed
    def request_json(self, method, url, **kwargs):
        if self.failed:
            return {"status":"failed","data":None,"error":{"type":"Offline","message":"offline"}}
        if url.endswith("/symbol"):
            return {"status":"success","data":{"Recommend.All":0.4}}
        if "/ideas/" in url:
            return {"status":"success","data":{"data":{"ideas":{"data":{"items":[{"name":"idea"}]}}}}}
        return {"status":"success","data":{"totalCount":0,"data":[]}}

def inp(symbol="BINANCE:BTCUSDT"): return TradingViewMarketInput(symbol=symbol)
def snapshot_success(*args, **kwargs):
    return {"status":"success","external_signals":{"technicals":{"status":"success","data":{"Recommend.All":.4}}},"error":None}
def ideas_success(*args, **kwargs): return {"status":"success","data":{"items":[{"name":"idea"}]},"error":None}
def test_disabled_by_default_returns_standard_failure(tmp_path):
    out=tradingview_market_intelligence(TradingViewMarketConfig(),inp(),tmp_path,transport=StubTransport())
    assert out["status"]=="failed" and out["error"]["type"]=="FeatureDisabled"
    assert out["fusion_integrated"] is False and out["trading_enabled"] is False
    assert {"fingerprint","decision_trail","cost_usd"} <= set(out)
def test_enabled_transaction_writes_report_and_decision_trail(tmp_path):
    out=tradingview_market_intelligence(TradingViewMarketConfig(enabled=True),inp(),tmp_path,
        transport=StubTransport(),snapshot_fn=snapshot_success,ideas_fn=ideas_success)
    assert out["status"]=="completed"
    assert Path(out["report_path"]).is_file() and Path(out["decision_trail"]["path"]).is_file()
    assert out["findings"]["external_signals"]["ideas"]["status"]=="success"
def test_result_never_claims_fusion_integration_or_trading(tmp_path):
    out=tradingview_market_intelligence(TradingViewMarketConfig(enabled=True),inp(),tmp_path,
        transport=StubTransport(),snapshot_fn=snapshot_success,ideas_fn=ideas_success)
    assert out["fusion_integrated"] is False and out["trading_enabled"] is False
def test_total_provider_failure_returns_value_not_exception(tmp_path):
    fail=lambda *a,**kw:{"status":"failed","data":None,"error":{"type":"Offline","message":"offline"}}
    out=tradingview_market_intelligence(TradingViewMarketConfig(enabled=True),inp(),tmp_path,
        transport=StubTransport(True),snapshot_fn=fail,ideas_fn=fail)
    assert out["status"]=="failed" and out["error"]
def test_fingerprint_is_stable_and_symbol_bound(tmp_path):
    cfg=TradingViewMarketConfig(enabled=True)
    kw={"snapshot_fn":snapshot_success,"ideas_fn":ideas_success}
    a=tradingview_market_intelligence(cfg,inp(),tmp_path/"a",**kw)["fingerprint"]
    b=tradingview_market_intelligence(cfg,inp(),tmp_path/"b",**kw)["fingerprint"]
    c=tradingview_market_intelligence(cfg,inp("BINANCE:ETHUSDT"),tmp_path/"c",**kw)["fingerprint"]
    assert a==b and a!=c
def test_step_callback_contains_no_personal_identifiers(tmp_path):
    events=[]
    tradingview_market_intelligence(TradingViewMarketConfig(enabled=True),inp(),tmp_path,on_step=events.append,
        snapshot_fn=snapshot_success,ideas_fn=ideas_success)
    assert events==[{"step":"sources_collected","status":"success","source_count":2}]
def test_input_requires_exchange_prefix():
    with pytest.raises(Exception): TradingViewMarketInput(symbol="BTCUSDT")
def test_manifest_declares_pillars_and_pii_free_fingerprint_fields():
    from omodul.tradingview_market_intelligence import __manifest__
    assert set(__manifest__["enabled_pillars"])=={"fingerprint","decision_trail","report"}
    assert "user_id" not in __manifest__["fingerprint_fields"]
def test_provider_exception_becomes_failure(tmp_path):
    def crash(*args,**kwargs): raise OSError("offline")
    out=tradingview_market_intelligence(TradingViewMarketConfig(enabled=True),inp(),tmp_path,snapshot_fn=crash)
    assert out["status"]=="failed" and out["error"]["type"]=="OSError"
