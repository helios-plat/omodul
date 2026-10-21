"""Opt-in, read-only TradingView intelligence transaction for Helios sidecar use."""
from __future__ import annotations
from pathlib import Path
from typing import Any, Callable, ClassVar
from pydantic import Field
from omodul._base import BaseConfig, Trail, build_result, compute_fingerprint, write_report
from oskill.tradingview_market_snapshot import tradingview_market_snapshot
from oprim._tv_transport import TVTransport
from oprim.tv_fetch_ideas import fetch_tv_ideas

__omodul_layer__ = "business_transaction"

class TradingViewMarketConfig(BaseConfig):
    """Disabled by default; callers must explicitly opt in."""
    enabled: bool = False
    quote: str = "USDT"
    timeout_seconds: float = Field(default=5.0, gt=0, le=15)
    market_limit: int = Field(default=20, ge=1, le=200)
    community_limit: int = Field(default=10, ge=1, le=100)
    news_limit: int = Field(default=10, ge=1, le=100)
    _omodul_name: ClassVar[str] = "tradingview_market_intelligence"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "decision_trail", "report"}
    # Explicit allowlist of non-PII deduplication fields.
    _fingerprint_fields: ClassVar[set[str]] = {"symbol", "quote", "market_limit", "community_limit", "news_limit"}

class TradingViewMarketInput(__import__("pydantic").BaseModel):
    """Normalized exchange-qualified ticker only; deliberately excludes user identifiers."""
    symbol: str = Field(min_length=3, max_length=80, pattern=r"^[A-Za-z0-9._-]+:[A-Za-z0-9._-]+$")


def tradingview_market_intelligence(config: TradingViewMarketConfig, input_data: TradingViewMarketInput,
                                    output_dir: Path, *, on_step: Callable[[dict], None] | None = None,
                                    transport: TVTransport | None = None,
                                    snapshot_fn: Callable[..., dict] | None = None,
                                    ideas_fn: Callable[..., dict] | None = None) -> dict:
    """Compose one oskill and an independent oprim, returning standard status/error/fingerprint/trail/report.

    Data remains an informational external sidecar. No Fusion Score dimension, weight,
    alert threshold, or trade-execution path is read or modified by this transaction.
    """
    output_dir = Path(output_dir)
    fingerprint = compute_fingerprint({"symbol": input_data.symbol, "quote": config.quote,
        "market_limit": config.market_limit, "community_limit": config.community_limit, "news_limit": config.news_limit})
    trail = Trail()
    if not config.enabled:
        trail.record(event="disabled", source="tradingview")
        return build_result(status="failed", error={"type": "FeatureDisabled", "message": "TradingView sidecar is disabled"},
            fingerprint=fingerprint, trail=trail, findings=None, fusion_integrated=False, trading_enabled=False, cost_usd=0.0)
    try:
        provider = transport or TVTransport(timeout=config.timeout_seconds)
        snapshot = (snapshot_fn or tradingview_market_snapshot)(input_data.symbol, quote=config.quote,
            market_limit=config.market_limit, community_limit=config.community_limit, news_limit=config.news_limit,
            transport=provider)
        # Ideas is a separate atomic provider operation, not duplicated inside the skill.
        _, ticker = input_data.symbol.split(":", 1)
        ideas = (ideas_fn or fetch_tv_ideas)(ticker, sort="recent", page=1, transport=provider)
        sources = dict(snapshot.get("external_signals", {})) if isinstance(snapshot, dict) else {}
        sources["ideas"] = ideas
        successful = sum(isinstance(value, dict) and value.get("status") in {"success", "degraded"} for value in sources.values())
        findings = {"status": "success" if successful else "failed", "symbol": input_data.symbol,
            "external_signals": sources, "fusion_integrated": False, "trading_enabled": False, "source": "tradingview"}
        if not successful:
            error = snapshot.get("error") if isinstance(snapshot, dict) else None
            trail.record(event="provider_failed", source="tradingview")
            if on_step:
                on_step({"step": "provider_failed", "status": "failed", "source": "tradingview"})
            return build_result(status="failed", error=error or {"type": "ProviderUnavailable", "message": "No TradingView source succeeded"},
                fingerprint=fingerprint, trail=trail, findings=findings, fusion_integrated=False, trading_enabled=False, cost_usd=0.0)
        trail.record(event="sources_collected", sources=sorted(sources), successful_sources=successful)
        if on_step:
            on_step({"step": "sources_collected", "status": "success", "source_count": len(sources)})
        output_dir.mkdir(parents=True, exist_ok=True)
        lines = ["# TradingView market-intelligence sidecar", "", f"- Symbol: `{input_data.symbol}`",
                 f"- Quote: `{config.quote}`", f"- Source count: `{len(sources)}`",
                 "- Fusion Score modified: `false`", "- Trading enabled: `false`", ""]
        for name, item in sources.items():
            lines.extend([f"## {name}", f"- Status: `{item.get('status', 'unknown')}`"])
            if item.get("stale"):
                lines.append("- Cache state: `stale/degraded`")
            error = item.get("error")
            if isinstance(error, dict):
                lines.append(f"- Error type: `{error.get('type', 'ProviderError')}`")
            lines.append("")
        report_path = write_report("\n".join(lines), output_dir=output_dir,
            name=f"tv_market_intelligence_{fingerprint}", fmt="markdown")
        trail.record(event="report_written", file=report_path.name)
        trail_path = trail.write(output_dir)
        return build_result(status="completed", error=None, fingerprint=fingerprint, trail=trail,
            trail_path=trail_path, report_path=report_path, findings=findings,
            fusion_integrated=False, trading_enabled=False, cost_usd=0.0)
    except Exception as exc:
        trail.record(event="failed", error_type=type(exc).__name__)
        return build_result(status="failed", error={"type": type(exc).__name__, "message": str(exc)[:400],
            "source": "tradingview"}, fingerprint=fingerprint, trail=trail, findings=None,
            fusion_integrated=False, trading_enabled=False, cost_usd=0.0)


__manifest__ = {"package": "omodul", "element": "tradingview_market_intelligence", "version": "1.0.0",
                "enabled_pillars": sorted(TradingViewMarketConfig._enabled_pillars),
                "fingerprint_fields": sorted(TradingViewMarketConfig._fingerprint_fields)}
