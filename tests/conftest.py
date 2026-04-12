"""pytest fixtures for btc-smc-bot tests."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from connectors.binance_client import Candle


def make_candle(
    close: float,
    open_: float | None = None,
    high: float | None = None,
    low: float | None = None,
    volume: int = 1000,
    instrument: str = "BTC_USD",
    ts_offset: int = 0,
) -> Candle:
    """Build a synthetic Candle for tests."""
    c = close
    return Candle(
        instrument=instrument,
        timestamp=datetime(2025, 1, 1, 12 + ts_offset, 0, 0, tzinfo=timezone.utc),
        open=open_ if open_ is not None else c * 0.999,
        high=high if high is not None else c * 1.002,
        low=low if low is not None else c * 0.997,
        close=c,
        volume=volume,
    )


@pytest.fixture
def btc_candles_bullish() -> list[Candle]:
    """100 uptrending BTC H1 candles starting around $50,000."""
    candles = []
    price = 50_000.0
    for i in range(100):
        price += 30.0  # slight uptrend
        candles.append(
            Candle(
                instrument="BTC_USD",
                timestamp=datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc).replace(
                    hour=i % 24, day=1 + i // 24
                ),
                open=price - 20,
                high=price + 50,
                low=price - 60,
                close=price,
                volume=1500 if i % 5 == 0 else 800,
            )
        )
    return candles


@pytest.fixture
def btc_candles_bearish() -> list[Candle]:
    """100 downtrending BTC H1 candles starting around $50,000."""
    candles = []
    price = 50_000.0
    for i in range(100):
        price -= 30.0
        candles.append(
            Candle(
                instrument="BTC_USD",
                timestamp=datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc).replace(
                    hour=i % 24, day=1 + i // 24
                ),
                open=price + 20,
                high=price + 60,
                low=price - 50,
                close=price,
                volume=1500 if i % 5 == 0 else 800,
            )
        )
    return candles


@pytest.fixture
def btc_candles_minimal() -> list[Candle]:
    """Exactly 14 BTC candles (minimum for ATR)."""
    price = 50_000.0
    return [
        Candle(
            instrument="BTC_USD",
            timestamp=datetime(2025, 1, 1, i, 0, 0, tzinfo=timezone.utc),
            open=price - 10 * i,
            high=price + 50,
            low=price - 100,
            close=price - 5 * i,
            volume=1000,
        )
        for i in range(14)
    ]
