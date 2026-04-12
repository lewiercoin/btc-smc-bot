"""Tests for agents/risk_verifier.py — deterministic risk gate."""

from __future__ import annotations

import pytest

from agents.risk_verifier import RiskVerifier, RiskVerifierResult


@pytest.fixture
def verifier() -> RiskVerifier:
    return RiskVerifier()


def _base_ctx(**overrides) -> dict:
    ctx = {
        "instrument":         "BTC_USD",
        "direction":          "bullish",
        "entry":              50_000.0,
        "stop_loss":          49_500.0,
        "position_size_lots": 0.002,
        "account_balance":    10_000.0,
        "open_positions":     [],
        "daily_loss_pct":     0.0,
        "confluence_score":   70,
    }
    ctx.update(overrides)
    return ctx


class TestNormalApproval:
    def test_clean_signal_approved(self, verifier):
        result = verifier.verify(_base_ctx())
        assert result.risk_approved is True
        assert result.rejection_reason == ""
        assert result.position_size > 0

    def test_no_exception_on_empty_input(self, verifier):
        result = verifier.verify({})
        assert isinstance(result, RiskVerifierResult)


class TestDailyLossCircuitBreaker:
    def test_5pct_loss_blocks(self, verifier):
        result = verifier.verify(_base_ctx(daily_loss_pct=0.05))
        assert result.risk_approved is False
        assert result.circuit_breaker_hit == "daily_loss"

    def test_6pct_loss_blocks(self, verifier):
        result = verifier.verify(_base_ctx(daily_loss_pct=0.06))
        assert result.risk_approved is False

    def test_3pct_loss_warns_not_blocks(self, verifier):
        result = verifier.verify(_base_ctx(daily_loss_pct=0.03))
        assert result.risk_approved is True
        assert any("WARNING" in n for n in result.risk_notes)

    def test_4pct_loss_warns_not_blocks(self, verifier):
        result = verifier.verify(_base_ctx(daily_loss_pct=0.04))
        assert result.risk_approved is True

    def test_under_3pct_no_warning(self, verifier):
        result = verifier.verify(_base_ctx(daily_loss_pct=0.02))
        assert result.risk_approved is True
        assert not any("WARNING" in n for n in result.risk_notes)


class TestMaxPositions:
    def test_two_positions_allowed(self, verifier):
        open_pos = [
            {"instrument": "BTC_USD", "direction": "bearish"},
        ]
        result = verifier.verify(_base_ctx(open_positions=open_pos))
        assert result.risk_approved is True  # 1 bearish, adding 1 bullish → OK

    def test_two_positions_same_direction_blocks_on_duplicate(self, verifier):
        open_pos = [
            {"instrument": "BTC_USD", "direction": "bullish"},
        ]
        result = verifier.verify(_base_ctx(open_positions=open_pos, direction="bullish"))
        assert result.risk_approved is False
        assert result.portfolio_corr_blocked is True

    def test_max_positions_reached_blocks(self, verifier):
        open_pos = [
            {"instrument": "BTC_USD", "direction": "bullish"},
            {"instrument": "BTC_USD", "direction": "bearish"},
        ]
        result = verifier.verify(_base_ctx(open_positions=open_pos, direction="bullish"))
        # 2 open positions → max_positions=2 → should block
        assert result.risk_approved is False
        assert result.circuit_breaker_hit == "max_positions"


class TestSizingValidation:
    def test_oversized_position_scaled_down(self, verifier):
        # 0.1 BTC with $500 SL on $10k account = 5% risk (exceeds 1%)
        result = verifier.verify(_base_ctx(
            entry=50_000.0,
            stop_loss=49_500.0,   # $500 SL
            position_size_lots=0.1,  # 0.1 × $500 = $50 risk (ok)
            account_balance=1_000.0, # on $1k account = 5% risk → scaled down
        ))
        assert result.risk_approved is True
        assert any("ALERT" in n for n in result.risk_notes)
        assert result.position_size < 0.1

    def test_correctly_sized_not_modified(self, verifier):
        # 0.001 BTC with $500 SL on $10k account = 0.005% risk (well under 1%)
        result = verifier.verify(_base_ctx(
            entry=50_000.0,
            stop_loss=49_500.0,
            position_size_lots=0.001,
            account_balance=10_000.0,
        ))
        assert result.risk_approved is True
        assert result.position_size == pytest.approx(0.001, abs=1e-6)


class TestSpreadZScore:
    def test_no_history_returns_none_zscore(self, verifier):
        result = verifier.verify(_base_ctx(current_spread=50.0, spread_history=None))
        assert result.spread_z_score is None

    def test_high_spread_zscore_adds_warning(self, verifier):
        # Need non-zero stdev: mix of values around mean=10
        history = [8.0, 9.0, 10.0, 11.0, 12.0, 10.0, 9.0, 11.0, 10.0, 10.0]
        # current_spread = 100 is far above mean=10 → z > 2
        result = verifier.verify(_base_ctx(current_spread=100.0, spread_history=history))
        assert result.spread_z_score is not None
        assert result.spread_z_score > 2.0
        assert any("WARNING" in n for n in result.risk_notes)

    def test_normal_spread_no_warning(self, verifier):
        history = [50.0, 48.0, 52.0, 49.0, 51.0, 50.0]
        result = verifier.verify(_base_ctx(current_spread=50.0, spread_history=history))
        assert result.spread_z_score is not None
        assert result.spread_z_score < 2.0
