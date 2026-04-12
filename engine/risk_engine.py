"""Risk Engine — position sizing, SL/TP calculation, spread filter.

Futures-adapted version for BTC/USDT perpetual on Binance.

Key differences from Forex version:
- position_size.lots = BTC contracts (not Forex lots)
- MAX_RISK_PCT = 1% (not 2% — futures leverage amplifies losses)
- Leverage-aware margin calculation
- Liquidation price estimation
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import structlog

from smc.utils import calculate_atr_series

if TYPE_CHECKING:
    from connectors.binance_client import Candle
    from smc.swing_detector import SwingResult

logger = structlog.get_logger(__name__)

# ── BTC Futures constants ─────────────────────────────────────────────────────

# Only BTC_USD supported in this bot
PAIR = "BTC_USD"

MIN_SL_DISTANCE_USD = 50.0    # $50 minimum SL distance
MAX_SPREAD_USD      = 50.0    # $50 maximum spread
TP_RATIOS           = (1.5, 2.5, 5.5)  # R multiples: TP1, TP2, TP3

MAX_LEVERAGE   = 3             # max leverage (isolated margin mode)
MAX_RISK_PCT   = 0.01          # 1% per trade
MIN_CONTRACTS  = 0.001         # min BTC contracts (Binance minimum)
MAX_CONTRACTS  = 100.0         # max BTC contracts

_ATR_PERIOD              = 14
_ATR_FALLBACK_MULTIPLIER = 2.0
_ATR_BUFFER_MULTIPLIER   = 0.5


# ── Dataclasses ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TakeProfitLevels:
    """Three TP levels based on risk:reward ratios."""

    tp1: float
    tp2: float
    tp3: float
    ratios: tuple[float, float, float]  # (1.5, 2.5, 5.5) for BTC


@dataclass(frozen=True)
class PositionSize:
    """Position sizing result — futures contracts."""

    lots: float         # BTC contracts (same field name for Signal compatibility)
    risk_amount: float  # USD risked
    risk_pct: float     # fraction of account risked (0.01 = 1%)
    sl_pips: float      # SL distance in USD (pip_size=1 for BTC futures)
    leverage: int       # recommended leverage
    margin_required: float  # USD margin required at given leverage


@dataclass(frozen=True)
class SpreadCheck:
    """Spread filter result."""

    passed: bool
    current_spread: float | None
    max_allowed: float
    reason: str = ""


@dataclass(frozen=True)
class TradeParameters:
    """Complete trade parameters returned by RiskEngine."""

    pair: str
    direction: str              # "bullish" | "bearish"
    entry: float
    stop_loss: float
    take_profits: TakeProfitLevels
    position_size: PositionSize
    spread_check: SpreadCheck
    risk_reward_ratio: float    # TP1 distance / SL distance
    sl_distance: float          # |entry - SL| in USD
    atr_at_entry: float
    liquidation_price: float    # estimated liquidation at max_leverage
    is_valid: bool
    rejection_reason: str = ""
    timestamp: datetime | None = field(default=None)


# ── RiskEngine ────────────────────────────────────────────────────────────────


class RiskEngine:
    """BTC Futures risk engine.

    Calculates SL (ATR-buffered swing), TP (R-based), position size (1% risk),
    liquidation price, and validates trade viability.
    """

    def __init__(
        self,
        max_risk_pct: float = MAX_RISK_PCT,
        max_leverage: int = MAX_LEVERAGE,
        default_balance: float = 1000.0,
    ) -> None:
        self.max_risk_pct = max_risk_pct
        self.max_leverage = max_leverage
        self.default_balance = default_balance
        self.logger = logger.bind(module="risk_engine")

    # ── Public interface ──────────────────────────────────────────────────────

    def calculate_trade(
        self,
        candles: list[Candle],
        setup_direction: str,
        pair: str = PAIR,
        account_balance: float | None = None,
        swings: SwingResult | None = None,
        current_spread: float | None = None,
    ) -> TradeParameters | None:
        """Orchestrate full trade parameter calculation.

        Args:
            candles: OHLCV candle list (minimum 14 candles).
            setup_direction: "bullish" or "bearish".
            pair: Instrument key (only "BTC_USD" supported).
            account_balance: Futures account balance in USDT.
            swings: Pre-computed SwingResult; ATR fallback used if None.
            current_spread: Current spread in USD; skips check if None.

        Returns:
            TradeParameters if trade is viable, None if critical error occurs.
        """
        if len(candles) < _ATR_PERIOD:
            self.logger.warning(
                "insufficient_candles_for_risk_engine",
                count=len(candles),
                required=_ATR_PERIOD,
            )
            return None

        balance = account_balance if account_balance is not None else self.default_balance

        entry = candles[-1].close

        atr = self._get_last_atr(candles)
        if atr == 0.0:
            self.logger.warning("atr_is_zero", pair=pair, candle_count=len(candles))
            return None

        stop_loss = self._calculate_stop_loss(candles, setup_direction, swings)

        # Enforce minimum SL distance
        sl_distance = abs(entry - stop_loss)
        if sl_distance < MIN_SL_DISTANCE_USD:
            sl_distance = MIN_SL_DISTANCE_USD
            if setup_direction == "bullish":
                stop_loss = entry - MIN_SL_DISTANCE_USD
            else:
                stop_loss = entry + MIN_SL_DISTANCE_USD

        take_profits = self._calculate_take_profits(entry, stop_loss, setup_direction)

        spread_check = self._check_spread(current_spread)

        position_size = self._calculate_position_size(balance, entry, sl_distance)

        risk_distance = abs(entry - stop_loss)
        tp1_distance  = abs(take_profits.tp1 - entry)
        rr_ratio      = tp1_distance / risk_distance if risk_distance > 0 else 0.0

        liq_price = self._estimate_liquidation(entry, stop_loss, setup_direction)

        is_valid, rejection = self._validate_trade(
            entry, stop_loss, take_profits, position_size, spread_check
        )

        params = TradeParameters(
            pair=pair,
            direction=setup_direction,
            entry=entry,
            stop_loss=stop_loss,
            take_profits=take_profits,
            position_size=position_size,
            spread_check=spread_check,
            risk_reward_ratio=rr_ratio,
            sl_distance=sl_distance,
            atr_at_entry=atr,
            liquidation_price=liq_price,
            is_valid=is_valid,
            rejection_reason=rejection,
            timestamp=datetime.now(tz=timezone.utc),
        )

        self.logger.info(
            "trade_calculated",
            pair=pair,
            direction=setup_direction,
            entry=entry,
            stop_loss=stop_loss,
            sl_distance=sl_distance,
            rr_ratio=rr_ratio,
            contracts=position_size.lots,
            is_valid=is_valid,
            rejection_reason=rejection or None,
        )

        return params

    # ── SL calculation ────────────────────────────────────────────────────────

    def _calculate_stop_loss(
        self,
        candles: list[Candle],
        setup_direction: str,
        swings: SwingResult | None,
    ) -> float:
        entry = candles[-1].close
        atr   = self._get_last_atr(candles)
        buffer = _ATR_BUFFER_MULTIPLIER * atr

        if swings is None or (not swings.lows and not swings.highs):
            if setup_direction == "bullish":
                return entry - (_ATR_FALLBACK_MULTIPLIER * atr)
            return entry + (_ATR_FALLBACK_MULTIPLIER * atr)

        if setup_direction == "bullish":
            lows_below = [sp for sp in swings.lows if sp.price < entry]
            if not lows_below:
                return entry - (_ATR_FALLBACK_MULTIPLIER * atr)
            nearest_low = max(lows_below, key=lambda sp: sp.price)
            return nearest_low.price - buffer

        highs_above = [sp for sp in swings.highs if sp.price > entry]
        if not highs_above:
            return entry + (_ATR_FALLBACK_MULTIPLIER * atr)
        nearest_high = min(highs_above, key=lambda sp: sp.price)
        return nearest_high.price + buffer

    # ── TP calculation ────────────────────────────────────────────────────────

    def _calculate_take_profits(
        self,
        entry: float,
        sl: float,
        setup_direction: str,
    ) -> TakeProfitLevels:
        r1, r2, r3 = TP_RATIOS

        if setup_direction == "bullish":
            risk = entry - sl
            return TakeProfitLevels(
                tp1=entry + risk * r1,
                tp2=entry + risk * r2,
                tp3=entry + risk * r3,
                ratios=TP_RATIOS,
            )

        risk = sl - entry
        return TakeProfitLevels(
            tp1=entry - risk * r1,
            tp2=entry - risk * r2,
            tp3=entry - risk * r3,
            ratios=TP_RATIOS,
        )

    # ── Position sizing ───────────────────────────────────────────────────────

    def _calculate_position_size(
        self,
        account_balance: float,
        entry: float,
        sl_distance: float,
    ) -> PositionSize:
        """Calculate BTC contracts using 1% fixed risk rule.

        contracts = risk_amount / sl_distance
        margin    = contracts * entry / leverage
        """
        risk_amount = account_balance * self.max_risk_pct

        if sl_distance <= 0:
            return PositionSize(
                lots=MIN_CONTRACTS,
                risk_amount=risk_amount,
                risk_pct=self.max_risk_pct,
                sl_pips=0.0,
                leverage=self.max_leverage,
                margin_required=0.0,
            )

        contracts = risk_amount / sl_distance
        contracts = max(MIN_CONTRACTS, min(MAX_CONTRACTS, round(contracts, 4)))

        margin = contracts * entry / self.max_leverage

        return PositionSize(
            lots=contracts,
            risk_amount=risk_amount,
            risk_pct=self.max_risk_pct,
            sl_pips=sl_distance,  # USD distance (pip_size=1 for BTC)
            leverage=self.max_leverage,
            margin_required=round(margin, 2),
        )

    # ── Spread filter ─────────────────────────────────────────────────────────

    def _check_spread(self, current_spread: float | None) -> SpreadCheck:
        if current_spread is None:
            return SpreadCheck(
                passed=True,
                current_spread=None,
                max_allowed=MAX_SPREAD_USD,
                reason="No spread data available — assuming acceptable",
            )

        if current_spread > MAX_SPREAD_USD:
            return SpreadCheck(
                passed=False,
                current_spread=current_spread,
                max_allowed=MAX_SPREAD_USD,
                reason=f"Spread ${current_spread:.2f} exceeds max ${MAX_SPREAD_USD:.2f}",
            )

        return SpreadCheck(
            passed=True,
            current_spread=current_spread,
            max_allowed=MAX_SPREAD_USD,
            reason="",
        )

    # ── Liquidation estimate ──────────────────────────────────────────────────

    def _estimate_liquidation(
        self,
        entry: float,
        stop_loss: float,
        setup_direction: str,
    ) -> float:
        """Estimate isolated-margin liquidation price (simplified Binance formula).

        Liquidation ≈ entry * (1 - 1/leverage) for LONG
                    ≈ entry * (1 + 1/leverage) for SHORT
        """
        margin_rate = 1.0 / self.max_leverage
        if setup_direction == "bullish":
            return round(entry * (1.0 - margin_rate), 2)
        return round(entry * (1.0 + margin_rate), 2)

    # ── Final validation ──────────────────────────────────────────────────────

    def _validate_trade(
        self,
        entry: float,
        sl: float,
        tp_levels: TakeProfitLevels,
        position_size: PositionSize,
        spread_check: SpreadCheck,
    ) -> tuple[bool, str]:
        if not spread_check.passed:
            return False, "Spread too wide"

        risk_distance = abs(entry - sl)
        tp1_distance  = abs(tp_levels.tp1 - entry)
        rr = tp1_distance / risk_distance if risk_distance > 0 else 0.0
        if rr < 1.0:
            return False, "Risk:reward below minimum (1.0)"

        if position_size.lots < MIN_CONTRACTS:
            return False, "Position too small"

        if position_size.lots > MAX_CONTRACTS:
            return False, "Position too large"

        if abs(entry - sl) < MIN_SL_DISTANCE_USD:
            return False, "SL too close"

        return True, ""

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_last_atr(self, candles: list[Candle]) -> float:
        atr_series = calculate_atr_series(candles, _ATR_PERIOD)
        for value in reversed(atr_series):
            if value is not None:
                return value
        return 0.0
