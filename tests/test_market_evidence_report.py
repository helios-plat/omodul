from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from omodul.market_evidence_report import (
    MarketEvidenceReportConfig,
    compute_fingerprint_for,
    market_evidence_report,
)

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)


def _source(source_id="primary-1", **kwargs):
    item = {
        "source_id": source_id,
        "source_type": "primary_exchange",
        "source_uri": f"https://exchange.example/candles/{source_id}",
        "retrieved_at_utc": "2026-10-22T11:00:00Z",
        "payload": {"symbol": "BTC-USDT", "close": "67000.10"},
        "provider": "test-exchange",
        "directness": 1.0,
    }
    item.update(kwargs)
    return item


def _config(**kwargs):
    return MarketEvidenceReportConfig(as_of_date="2026-10-22", market="crypto", **kwargs)


def test_report_creates_fingerprint_trail_markdown_and_ledger(tmp_path: Path):
    result = market_evidence_report(_config(), {"sources": [_source()], "now_utc": NOW}, tmp_path)
    assert result["status"] == "completed"
    assert len(result["fingerprint"]) == 64
    assert result["decision_trail"]["status"] == "completed"
    assert result["decision_trail"]["steps"]
    assert result["report_path"].is_file()
    assert result["ledger_path"].is_file()
    assert "Quality index" in result["report_path"].read_text(encoding="utf-8")
    assert len(result["evidence_records"]) == 1
    ledger_row = json.loads(result["ledger_path"].read_text(encoding="utf-8").splitlines()[0])
    assert ledger_row["evidence_id"] == result["evidence_records"][0]["evidence_id"]


def test_conflict_is_reported_as_review_status_not_hidden_as_workflow_failure(tmp_path: Path):
    claims = [
        {"subject": "BTC-USDT", "predicate": "close", "value": 100, "unit": "USDT", "source_id": "a"},
        {"subject": "BTC-USDT", "predicate": "close", "value": 102, "unit": "USDT", "source_id": "b"},
    ]
    result = market_evidence_report(_config(), {"sources": [_source("a"), _source("b")], "claims": claims, "now_utc": NOW}, tmp_path)
    assert result["status"] == "completed"
    assert result["findings"]["review"]["status"] == "conflict"
    assert "**conflict**" in result["report_path"].read_text(encoding="utf-8")


def test_invalid_source_is_marked_unpersisted_in_report(tmp_path: Path):
    result = market_evidence_report(_config(), {"sources": [{"source_id": "bad"}], "now_utc": NOW}, tmp_path)
    assert result["status"] == "completed"
    assert result["findings"]["review"]["status"] == "unverified"
    assert result["findings"]["review"]["sources"][0]["ledger_persisted"] is False


def test_empty_input_completes_with_unverified_review(tmp_path: Path):
    result = market_evidence_report(_config(), {}, tmp_path)
    assert result["status"] == "completed"
    assert result["findings"]["review"]["status"] == "unverified"
    assert "No source records" in result["report_path"].read_text(encoding="utf-8")


def test_invalid_container_input_fails_closed_and_reports_failure(tmp_path: Path):
    result = market_evidence_report(_config(), None, tmp_path)
    assert result["status"] == "failed"
    assert result["error"]["type"] == "ValueError"
    assert result["decision_trail"]["status"] == "failed"
    assert result["report_path"].is_file()
    assert ".partial.md" in result["report_path"].name


def test_repeat_run_deduplicates_same_evidence_in_ledger(tmp_path: Path):
    inputs = {"sources": [_source()], "now_utc": NOW}
    first = market_evidence_report(_config(), inputs, tmp_path)
    second = market_evidence_report(_config(), inputs, tmp_path)
    assert first["ledger_path"] == second["ledger_path"]
    assert len(first["ledger_path"].read_text(encoding="utf-8").splitlines()) == 1
    assert second["evidence_records"][0]["appended"] is False


def test_fingerprint_uses_shared_3o_helper_and_is_deterministic():
    config = _config()
    input_data = {"sources": [_source()]}
    assert compute_fingerprint_for(config, input_data) == compute_fingerprint_for(config, input_data)


def test_pii_key_is_excluded_by_shared_fingerprint_helper():
    config = _config()
    a = {"sources": [_source()], "email": "one@example.invalid"}
    b = {"sources": [_source()], "email": "two@example.invalid"}
    assert compute_fingerprint_for(config, a) == compute_fingerprint_for(config, b)


def test_report_explains_no_vendor_or_order_actions(tmp_path: Path):
    result = market_evidence_report(_config(), {"sources": [_source()], "now_utc": NOW}, tmp_path)
    report = result["report_path"].read_text(encoding="utf-8")
    assert "does not fetch current market data" in report
    assert "no trading or order action" in report


def test_each_execution_writes_under_explicit_output_directory(tmp_path: Path):
    out = tmp_path / "safe-output"
    result = market_evidence_report(_config(), {"sources": [_source()], "now_utc": NOW}, out)
    assert result["report_path"].parent == out
    assert result["ledger_path"].parent == out


def test_config_rejects_invalid_date_type():
    with pytest.raises(Exception):
        _config(as_of_date=None)


def test_output_does_not_include_source_payload_in_decision_trail(tmp_path: Path):
    result = market_evidence_report(_config(), {"sources": [_source()], "now_utc": NOW}, tmp_path)
    serialized = json.dumps(result["decision_trail"], default=str)
    assert "67000.10" not in serialized
    assert result["report_path"].parent == tmp_path
