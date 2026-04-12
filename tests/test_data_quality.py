"""Tests for dq/data_quality.py."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from connectors.binance_client import Candle
from dq.data_quality import DataQualityChecker
from tests.conftest import make_candle


@pytest.fixture
def dq() -> DataQualityChecker:
    return DataQualityChecker()


class TestCheckCandles:
    def test_valid_100_candles_passes(self, dq, btc_candles_bullish):
        result = dq.check_candles(btc_candles_bullish)
        assert result.passed is True
        assert result.issues == []
        assert result.candles_total == 100

    def test_too_few_candles_fails(self, dq):
        candles = [make_candle(50_000.0 + i, ts_offset=i) for i in range(10)]
        result = dq.check_candles(candles)
        assert result.passed is False
        assert any("Too few" in issue for issue in result.issues)

    def test_exactly_99_candles_passes(self, dq):
        from datetime import timedelta
        base = datetime(2025, 1, 1, tzinfo=timezone.utc)
        candles = [
            Candle(
                instrument="BTC_USD",
                timestamp=base + timedelta(hours=i),
                open=50000.0,
                high=50100.0,
                low=49900.0,
                close=50000.0,
                volume=1000,
            )
            for i in range(99)
        ]
        result = dq.check_candles(candles)
        assert result.passed is True

    def test_invalid_ohlc_high_less_than_low(self, dq):
        bad_candle = Candle(
            instrument="BTC_USD",
            timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc),
            open=50000.0,
            high=49000.0,   # high < low — invalid
            low=50000.0,
            close=49500.0,
            volume=1000,
        )
        # Build 99 valid candles + 1 bad
        from datetime import timedelta
        base = datetime(2025, 1, 2, tzinfo=timezone.utc)
        valid = [
            Candle(
                instrument="BTC_USD",
                timestamp=base + timedelta(hours=i),
                open=50000.0, high=50100.0, low=49900.0, close=50000.0, volume=1000,
            )
            for i in range(99)
        ]
        result = dq.check_candles([bad_candle] + valid)
        assert result.passed is False

    def test_non_monotonic_timestamps_fails(self, dq):
        from datetime import timedelta
        base = datetime(2025, 1, 1, tzinfo=timezone.utc)
        candles = [
            Candle(
                instrument="BTC_USD",
                timestamp=base + timedelta(hours=i),
                open=50000.0, high=50100.0, low=49900.0, close=50000.0, volume=1000,
            )
            for i in range(99)
        ]
        # Insert a backwards timestamp at index 50
        candles[50] = Candle(
            instrument="BTC_USD",
            timestamp=base + timedelta(hours=30),  # earlier than candle[49]
            open=50000.0, high=50100.0, low=49900.0, close=50000.0, volume=1000,
        )
        result = dq.check_candles(candles)
        assert result.passed is False


class TestCheckSpread:
    def test_acceptable_spread_passes(self, dq):
        assert dq.check_spread(10.0) is True

    def test_zero_spread_passes(self, dq):
        assert dq.check_spread(0.0) is True

    def test_spread_at_limit_passes(self, dq):
        assert dq.check_spread(50.0) is True

    def test_spread_above_limit_fails(self, dq):
        assert dq.check_spread(51.0) is False

    def test_very_wide_spread_fails(self, dq):
        assert dq.check_spread(500.0) is False
