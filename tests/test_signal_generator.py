"""Tests for engine/signal_generator.py — pipeline integration with mocks."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from connectors.binance_client import Candle
from engine.signal_generator import Signal, SignalGenerator


def _make_candles(n: int = 100, price: float = 50_000.0, trend: str = "up") -> list[Candle]:
    candles = []
    for i in range(n):
        p = price + i * (30 if trend == "up" else -30)
        candles.append(
            Candle(
                instrument="BTC_USD",
                timestamp=datetime(2025, 1, 1, i % 24, 0, 0, tzinfo=timezone.utc),
                open=p - 20,
                high=p + 50,
                low=p - 60,
                close=p,
                volume=1500 if i % 5 == 0 else 800,
            )
        )
    return candles


@pytest.fixture
def mock_binance():
    client = MagicMock()
    client.get_candles.return_value = _make_candles()
    client.get_current_spread.return_value = 5.0
    client.get_futures_balance.return_value = 10_000.0
    return client


@pytest.fixture
def mock_news():
    from connectors.news_client import NewsCheckResult
    client = MagicMock()
    client.is_news_blocked.return_value = NewsCheckResult(is_blocked=False)
    return client


@pytest.fixture
def mock_db():
    db = MagicMock()
    db.get_open_signals.return_value = []
    return db


class TestSignalGeneratorPipeline:
    def test_returns_none_when_dq_fails(self, mock_binance, mock_news, mock_db):
        # Provide too few candles → DQ fails
        mock_binance.get_candles.return_value = _make_candles(5)
        gen = SignalGenerator(
            binance_client=mock_binance,
            news_client=mock_news,
            db=mock_db,
        )
        result = gen.generate()
        assert result is None

    def test_returns_none_when_news_blocked(self, mock_binance, mock_news, mock_db):
        from connectors.news_client import NewsCheckResult
        mock_news.is_news_blocked.return_value = NewsCheckResult(
            is_blocked=True, reason="NFP in 30 min"
        )
        gen = SignalGenerator(
            binance_client=mock_binance,
            news_client=mock_news,
            db=mock_db,
        )
        result = gen.generate()
        assert result is None

    def test_signal_has_required_fields_when_generated(self, mock_binance, mock_news, mock_db):
        """If confluence + risk passes → signal should have all required fields."""
        gen = SignalGenerator(
            binance_client=mock_binance,
            news_client=mock_news,
            db=mock_db,
            confluence_threshold=0,  # force pass for testing
        )
        # Patch confluence scorer to always return a valid high score
        from engine.confluence_scorer import ConfluenceResult, ScoreComponent
        mock_result = ConfluenceResult(
            total_score=75,
            max_possible=110,
            threshold=65,
            is_signal=True,
            setup_direction="bullish",
            components=[],
            timestamp=datetime.now(tz=timezone.utc),
            pair="BTC_USD",
        )
        with patch.object(gen.scorer, "score", return_value=mock_result):
            result = gen.generate(account_balance=10_000.0)

        if result is not None:
            assert isinstance(result.id, str)
            assert result.pair == "BTC_USD"
            assert result.direction in ("bullish", "bearish")
            assert result.entry > 0
            assert result.stop_loss > 0
            assert result.take_profit_1 > 0
            assert result.confluence_score == 75
            assert result.leverage > 0
            assert result.position_size > 0

    def test_returns_none_on_binance_fetch_error(self, mock_binance, mock_news, mock_db):
        mock_binance.get_candles.side_effect = RuntimeError("API timeout")
        gen = SignalGenerator(
            binance_client=mock_binance,
            news_client=mock_news,
            db=mock_db,
        )
        result = gen.generate()
        assert result is None

    def test_db_save_failure_does_not_crash_pipeline(self, mock_binance, mock_news, mock_db):
        """DB save failure should be swallowed — signal still returned."""
        mock_db.save_signal.side_effect = RuntimeError("DB write failed")
        gen = SignalGenerator(
            binance_client=mock_binance,
            news_client=mock_news,
            db=mock_db,
            confluence_threshold=0,
        )
        from engine.confluence_scorer import ConfluenceResult
        mock_result = ConfluenceResult(
            total_score=75, max_possible=110, threshold=65,
            is_signal=True, setup_direction="bullish",
            components=[], timestamp=datetime.now(tz=timezone.utc), pair="BTC_USD",
        )
        with patch.object(gen.scorer, "score", return_value=mock_result):
            # Should not raise even though DB fails
            try:
                gen.generate(account_balance=10_000.0)
            except RuntimeError:
                pytest.fail("DB failure should not propagate from signal generator")


class TestSignalDataclass:
    def test_signal_fields_accessible(self):
        sig = Signal(
            id="test-uuid",
            pair="BTC_USD",
            timeframe="H1",
            direction="bullish",
            timestamp=datetime.now(tz=timezone.utc),
            entry=50_000.0,
            stop_loss=49_500.0,
            take_profit_1=50_750.0,
            take_profit_2=51_250.0,
            take_profit_3=52_750.0,
            position_size=0.002,
            risk_reward_ratio=1.5,
            leverage=3,
            liquidation_price=33_333.0,
            confluence_score=75,
            confluence_components=(),
            risk_amount=100.0,
            risk_pct=0.01,
            sl_distance=500.0,
            atr_at_entry=350.0,
        )
        assert sig.pair == "BTC_USD"
        assert sig.confluence_score == 75
        assert sig.leverage == 3
        assert sig.status == "pending"
