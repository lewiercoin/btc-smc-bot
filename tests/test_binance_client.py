"""Tests for connectors/binance_client.py — Candle adapter."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from connectors.binance_client import BinanceClient, Candle


class TestCandle:
    def test_candle_is_frozen(self):
        c = Candle(
            instrument="BTC_USD",
            timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc),
            open=50000.0,
            high=50500.0,
            low=49800.0,
            close=50200.0,
            volume=1500,
        )
        with pytest.raises(Exception):
            c.close = 99999.0  # type: ignore[misc]

    def test_candle_fields(self):
        ts = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        c = Candle(
            instrument="BTC_USD", timestamp=ts,
            open=60000.0, high=60500.0, low=59500.0, close=60100.0, volume=2000,
        )
        assert c.instrument == "BTC_USD"
        assert c.open == 60000.0
        assert c.high == 60500.0
        assert c.low == 59500.0
        assert c.close == 60100.0
        assert c.volume == 2000


class TestBinanceClientAdapter:
    def test_to_candle_conversion(self):
        raw_kline = {
            "symbol": "BTCUSDT",
            "timeframe": "1h",
            "open_time": datetime(2025, 1, 1, 10, 0, tzinfo=timezone.utc),
            "open": 50000.0,
            "high": 50500.0,
            "low": 49800.0,
            "close": 50200.0,
            "volume": 123.456,
        }
        candle = BinanceClient._to_candle(raw_kline, "BTC_USD")
        assert candle.instrument == "BTC_USD"
        assert candle.open == 50000.0
        assert candle.volume == 123  # truncated to int

    def test_get_candles_drops_last_open_candle(self):
        """BinanceClient should drop the last (open) candle."""
        mock_client = MagicMock()
        # Return 101 candles (count + 1)
        from datetime import timedelta
        base = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        mock_client.fetch_klines.return_value = [
            {
                "open_time": base + timedelta(hours=i),
                "open": 50000.0, "high": 50100.0, "low": 49900.0,
                "close": 50050.0, "volume": 1000.0,
            }
            for i in range(101)
        ]
        with patch("connectors.binance_client.BinanceFuturesRestClient") as MockRest:
            MockRest.return_value = mock_client
            with patch("connectors.binance_client.RestClientConfig"):
                client = BinanceClient.__new__(BinanceClient)
                client._client = mock_client
                client.logger = MagicMock()
                candles = client.get_candles("BTC_USD", "H1", count=100)

        assert len(candles) == 100  # last open candle dropped

    def test_get_current_spread_returns_ask_minus_bid(self):
        mock_client = MagicMock()
        mock_client.fetch_book_ticker.return_value = {"bid": 50000.0, "ask": 50010.0}
        client = BinanceClient.__new__(BinanceClient)
        client._client = mock_client
        client.logger = MagicMock()
        spread = client.get_current_spread("BTC_USD")
        assert spread == pytest.approx(10.0)

    def test_get_spread_returns_zero_on_error(self):
        mock_client = MagicMock()
        mock_client.fetch_book_ticker.side_effect = RuntimeError("timeout")
        client = BinanceClient.__new__(BinanceClient)
        client._client = mock_client
        client.logger = MagicMock()
        spread = client.get_current_spread("BTC_USD")
        assert spread == 0.0
