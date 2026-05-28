"""Tests for cross_asset_opportunity_ranking omodul."""
import pytest
from omodul.cross_asset_opportunity_ranking import CrossAssetRankingConfig, cross_asset_opportunity_ranking


def test_basic(tmp_path):
    c = CrossAssetRankingConfig(asset_classes=["crypto", "commodity"], date="2024-06-01")
    inp = {
        "fusion_scores": {"BTC": 65, "Gold": 72, "ETH": 58},
        "fusion_histories": {"BTC": list(range(40, 80)), "Gold": list(range(30, 80)), "ETH": list(range(35, 70))},
    }
    r = cross_asset_opportunity_ranking(c, inp, tmp_path)
    assert r["status"] == "completed"
    assert r["findings"]["top_pick"] in ("BTC", "Gold", "ETH")

def test_report_generated(tmp_path):
    c = CrossAssetRankingConfig(date="2024-06-01")
    r = cross_asset_opportunity_ranking(c, {"fusion_scores": {"A": 80}, "fusion_histories": {"A": list(range(50, 90))}}, tmp_path)
    assert r["report_path"] is not None

def test_fingerprint_stable(tmp_path):
    c = CrossAssetRankingConfig(date="2024-06-01")
    inp = {"fusion_scores": {"X": 50}, "fusion_histories": {"X": [50]*30}}
    r1 = cross_asset_opportunity_ranking(c, inp, tmp_path)
    r2 = cross_asset_opportunity_ranking(c, inp, tmp_path)
    assert r1["fingerprint"] == r2["fingerprint"]
