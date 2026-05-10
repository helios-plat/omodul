"""Shared test fixtures for omodul - includes real financial data."""

import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REAL_DATA_DIR = Path(__file__).parent / "real_data"


@pytest.fixture
def rng():
    return np.random.default_rng(42)


@pytest.fixture
def spy_returns():
    """SPY-like 252-day daily returns (synthetic but realistic)."""
    path = REAL_DATA_DIR / "spy_252d_returns.csv"
    if path.exists():
        return pd.read_csv(path, index_col=0, parse_dates=True).squeeze()
    # Generate realistic SPY-like returns
    rng = np.random.default_rng(2024)
    dates = pd.bdate_range("2023-01-03", periods=252)
    returns = rng.normal(0.0004, 0.012, 252)  # ~10% annual, ~19% vol
    return pd.Series(returns, index=dates, name="SPY")


@pytest.fixture
def btc_panel():
    """BTC-like 1-year panel data (price, volume, volatility)."""
    path = REAL_DATA_DIR / "btc_1y_panel.csv"
    if path.exists():
        return pd.read_csv(path, index_col=0, parse_dates=True)
    rng = np.random.default_rng(2024)
    dates = pd.date_range("2023-01-01", periods=365, freq="D")
    price = 30000 * np.cumprod(1 + rng.normal(0.001, 0.03, 365))
    volume = rng.lognormal(20, 1, 365)
    vol = np.abs(rng.normal(0.03, 0.01, 365))
    return pd.DataFrame({"price": price, "volume": volume, "volatility": vol}, index=dates)


@pytest.fixture
def ff5_factors():
    """Fama-French 5-factor monthly data (synthetic but realistic)."""
    path = REAL_DATA_DIR / "ff5_factors_5y.csv"
    if path.exists():
        return pd.read_csv(path, index_col=0, parse_dates=True)
    rng = np.random.default_rng(2024)
    dates = pd.date_range("2019-01-01", periods=60, freq="ME")
    return pd.DataFrame({
        "Mkt-RF": rng.normal(0.008, 0.04, 60),
        "SMB": rng.normal(0.002, 0.03, 60),
        "HML": rng.normal(0.001, 0.03, 60),
        "RMW": rng.normal(0.003, 0.02, 60),
        "CMA": rng.normal(0.002, 0.02, 60),
    }, index=dates)


@pytest.fixture
def regime_labels(spy_returns):
    """Regime labels for SPY returns (BULL/BEAR/NEUTRAL)."""
    n = len(spy_returns)
    labels = []
    for i in range(n):
        if i < n // 3:
            labels.append("BULL")
        elif i < 2 * n // 3:
            labels.append("BEAR")
        else:
            labels.append("NEUTRAL")
    return pd.Series(labels, index=spy_returns.index)


@pytest.fixture
def sample_trades():
    """Sample trade journal data."""
    rng = np.random.default_rng(42)
    n = 100
    dates = pd.date_range("2023-01-01", periods=n, freq="B")
    return pd.DataFrame({
        "timestamp": dates,
        "symbol": rng.choice(["AAPL", "MSFT", "GOOGL", "AMZN"], n),
        "side": rng.choice(["buy", "sell"], n),
        "quantity": rng.integers(10, 1000, n),
        "price": rng.uniform(100, 500, n),
        "pnl": rng.normal(50, 200, n),
    })
