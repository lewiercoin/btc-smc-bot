"""Data Quality module for btc-smc-bot.

Validates Binance candles, spreads, and news blackout windows.
Adapted from smc-signal-bot — spread limits use USD (not pips).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import structlog

from connectors.binance_client import Candle

if TYPE_CHECKING:
    from connectors.news_client import NewsClient

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class DQResult:
    """Data Quality check result for candles."""

    passed: bool
    issues: list[str]
    candles_valid: int
    candles_total: int


@dataclass(frozen=True)
class NewsCheckResult:
    """News window check result."""

    blocked: bool
    reason: str | None = None
    next_clear: datetime | None = None


class DataQualityChecker:
    """Validates BTC/USDT Binance Futures market data before SMC processing."""

    # Spread limit in USD (Binance futures, not pips)
    SPREAD_LIMIT_USD = 50.0

    # Warning threshold
    SPREAD_WARNING_PCT = 0.8

    # Minimum candles required
    MIN_CANDLES = 99

    # Max gap multiplier (2x granularity)
    MAX_GAP_MULTIPLIER = 2

    # News blackout window
    NEWS_BLACKOUT_MINUTES = 120

    # Max allowed OHLC range multiplier vs ATR (loose sanity check)
    MAX_OHLC_RANGE_USD = 50_000.0  # $50k range on a single BTC candle = clearly bad data

    def __init__(self, news_client: NewsClient | None = None) -> None:
        self.news_client = news_client
        self.logger = logger.bind(module="data_quality")

    # ── Public interface ──────────────────────────────────────────────────────

    def check_candles(self, candles: list[Candle]) -> DQResult:
        """Validate candle list integrity.

        Checks:
        - Minimum count (99 candles)
        - OHLC sanity (high >= low, no NaN, no crazy range)
        - Timestamp monotonicity

        Args:
            candles: LTF candles to validate.

        Returns:
            DQResult with passed flag and list of issues.
        """
        issues: list[str] = []
        total = len(candles)

        if total < self.MIN_CANDLES:
            issues.append(f"Too few candles: {total} < {self.MIN_CANDLES}")
            return DQResult(passed=False, issues=issues, candles_valid=0, candles_total=total)

        valid_count = 0
        for i, c in enumerate(candles):
            candle_issues = self._check_single_candle(c, i)
            if candle_issues:
                issues.extend(candle_issues)
            else:
                valid_count += 1

        # Timestamp monotonicity
        ts_issues = self._check_timestamps(candles)
        issues.extend(ts_issues)

        passed = len(issues) == 0
        if not passed:
            self.logger.warning(
                "dq_candles_failed",
                total=total,
                valid=valid_count,
                issue_count=len(issues),
                first_issue=issues[0] if issues else None,
            )

        return DQResult(
            passed=passed,
            issues=issues,
            candles_valid=valid_count,
            candles_total=total,
        )

    def check_spread(self, spread_usd: float, pair: str = "BTC_USD") -> bool:
        """Check whether current Binance spread is within acceptable limits.

        Args:
            spread_usd: ask - bid in USD.
            pair: Instrument (unused, always BTC_USD in this bot).

        Returns:
            True if spread is acceptable.
        """
        if spread_usd > self.SPREAD_LIMIT_USD:
            self.logger.info(
                "dq_spread_rejected",
                spread_usd=spread_usd,
                max_usd=self.SPREAD_LIMIT_USD,
            )
            return False

        if spread_usd > self.SPREAD_LIMIT_USD * self.SPREAD_WARNING_PCT:
            self.logger.warning(
                "dq_spread_warning",
                spread_usd=spread_usd,
                limit_usd=self.SPREAD_LIMIT_USD,
            )

        return True

    # ── Private helpers ───────────────────────────────────────────────────────

    def _check_single_candle(self, c: Candle, idx: int) -> list[str]:
        issues: list[str] = []

        if c.high < c.low:
            issues.append(f"Candle[{idx}]: high {c.high} < low {c.low}")

        ohlc_values = [c.open, c.high, c.low, c.close]
        for val in ohlc_values:
            if val <= 0 or val != val:  # <= 0 or NaN
                issues.append(f"Candle[{idx}]: invalid OHLC value {val}")
                break

        if c.high - c.low > self.MAX_OHLC_RANGE_USD:
            issues.append(
                f"Candle[{idx}]: extreme range ${c.high - c.low:.0f} "
                f"(max allowed ${self.MAX_OHLC_RANGE_USD:.0f})"
            )

        return issues

    def _check_timestamps(self, candles: list[Candle]) -> list[str]:
        """Check that timestamps are strictly increasing."""
        issues: list[str] = []
        for i in range(1, len(candles)):
            if candles[i].timestamp <= candles[i - 1].timestamp:
                issues.append(
                    f"Timestamp not increasing at index {i}: "
                    f"{candles[i - 1].timestamp} → {candles[i].timestamp}"
                )
                if len(issues) >= 3:
                    break
        return issues
