"""Tests for engine/risk_engine.py — BTC futures position sizing."""

from __future__ import annotations

import pytest

from engine.risk_engine import (
    MAX_CONTRACTS,
    MAX_LEVERAGE,
    MAX_RISK_PCT,
    MAX_SPREAD_USD,
    MIN_CONTRACTS,
    MIN_SL_DISTANCE_USD,
    TP_RATIOS,
    RiskEngine,
    TradeParameters,
)
from tests.conftest import btc_candles_bullish, btc_candles_bearish  # noqa: F401


@pytest.fixture
def engine() -> RiskEngine:
    return RiskEngine(max_risk_pct=0.01, max_leverage=3, default_balance=10_000.0)


# ── SL/TP ─────────────────────────────────────────────────────────────────────

class TestSLCalculation:
    def test_bullish_sl_below_entry(self, engine, btc_candles_bullish):
        params = engine.calculate_trade(btc_candles_bullish, "bullish", account_balance=10_000.0)
        assert params is not None
        assert params.stop_loss < params.entry

    def test_bearish_sl_above_entry(self, engine, btc_candles_bearish):
        params = engine.calculate_trade(btc_candles_bearish, "bearish", account_balance=10_000.0)
        assert params is not None
        assert params.stop_loss > params.entry

    def test_sl_minimum_distance_enforced(self, engine, btc_candles_bullish):
        params = engine.calculate_trade(btc_candles_bullish, "bullish", account_balance=10_000.0)
        assert params is not None
        assert params.sl_distance >= MIN_SL_DISTANCE_USD

    def test_insufficient_candles_returns_none(self, engine):
        from tests.conftest import make_candle
        tiny = [make_candle(50_000.0 + i, ts_offset=i) for i in range(5)]
        result = engine.calculate_trade(tiny, "bullish")
        assert result is None


class TestTPCalculation:
    def test_bullish_tp_above_entry(self, engine, btc_candles_bullish):
        p = engine.calculate_trade(btc_candles_bullish, "bullish", account_balance=10_000.0)
        assert p is not None
        assert p.take_profits.tp1 > p.entry
        assert p.take_profits.tp2 > p.take_profits.tp1
        assert p.take_profits.tp3 > p.take_profits.tp2

    def test_bearish_tp_below_entry(self, engine, btc_candles_bearish):
        p = engine.calculate_trade(btc_candles_bearish, "bearish", account_balance=10_000.0)
        assert p is not None
        assert p.take_profits.tp1 < p.entry
        assert p.take_profits.tp2 < p.take_profits.tp1

    def test_tp_ratios_match_expected(self, engine, btc_candles_bullish):
        p = engine.calculate_trade(btc_candles_bullish, "bullish", account_balance=10_000.0)
        assert p is not None
        r = p.sl_distance
        r1, r2, r3 = TP_RATIOS
        assert abs(p.take_profits.tp1 - (p.entry + r * r1)) < 1.0
        assert abs(p.take_profits.tp2 - (p.entry + r * r2)) < 1.0
        assert abs(p.take_profits.tp3 - (p.entry + r * r3)) < 1.0


# ── Position sizing ───────────────────────────────────────────────────────────

class TestPositionSizing:
    def test_1pct_risk_respected(self, engine, btc_candles_bullish):
        balance = 10_000.0
        p = engine.calculate_trade(btc_candles_bullish, "bullish", account_balance=balance)
        assert p is not None
        # risk_usd = contracts * sl_distance ≈ max_risk_pct * balance
        actual_risk = p.position_size.lots * p.sl_distance
        assert actual_risk <= balance * MAX_RISK_PCT * 1.01  # 1% tolerance

    def test_contracts_within_bounds(self, engine, btc_candles_bullish):
        p = engine.calculate_trade(btc_candles_bullish, "bullish", account_balance=10_000.0)
        assert p is not None
        assert MIN_CONTRACTS <= p.position_size.lots <= MAX_CONTRACTS

    def test_leverage_matches_config(self, engine, btc_candles_bullish):
        p = engine.calculate_trade(btc_candles_bullish, "bullish", account_balance=10_000.0)
        assert p is not None
        assert p.position_size.leverage == MAX_LEVERAGE

    def test_margin_required_positive(self, engine, btc_candles_bullish):
        p = engine.calculate_trade(btc_candles_bullish, "bullish", account_balance=10_000.0)
        assert p is not None
        assert p.position_size.margin_required > 0


# ── Liquidation price ─────────────────────────────────────────────────────────

class TestLiquidationPrice:
    def test_bullish_liq_below_entry(self, engine, btc_candles_bullish):
        p = engine.calculate_trade(btc_candles_bullish, "bullish", account_balance=10_000.0)
        assert p is not None
        assert p.liquidation_price < p.entry

    def test_bearish_liq_above_entry(self, engine, btc_candles_bearish):
        p = engine.calculate_trade(btc_candles_bearish, "bearish", account_balance=10_000.0)
        assert p is not None
        assert p.liquidation_price > p.entry


# ── Spread check ──────────────────────────────────────────────────────────────

class TestSpreadCheck:
    def test_no_spread_passes(self, engine, btc_candles_bullish):
        p = engine.calculate_trade(btc_candles_bullish, "bullish", current_spread=None)
        assert p is not None
        assert p.spread_check.passed

    def test_wide_spread_fails_validation(self, engine, btc_candles_bullish):
        p = engine.calculate_trade(
            btc_candles_bullish, "bullish",
            account_balance=10_000.0,
            current_spread=MAX_SPREAD_USD + 1.0,
        )
        assert p is not None
        assert not p.is_valid
        assert "Spread" in p.rejection_reason


# ── RR Ratio ─────────────────────────────────────────────────────────────────

class TestRiskReward:
    def test_rr_positive(self, engine, btc_candles_bullish):
        p = engine.calculate_trade(btc_candles_bullish, "bullish", account_balance=10_000.0)
        assert p is not None
        assert p.risk_reward_ratio >= 1.0
