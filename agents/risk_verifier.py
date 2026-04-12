"""Risk Verifier — deterministyczny Agent 3 (bez LLM).

Futures-adapted wersja dla btc-smc-bot:
- MAX_POSITIONS = 2 (mniej niż 3 w smc-signal-bot)
- MAX_RISK_PCT  = 0.01 (1%, nie 2% — futures leverage)
- Tylko BTC_USD, więc korelacja portfela uproszczona

Nigdy nie rzuca wyjątku na zewnątrz.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

_DAILY_LOSS_HARD = 0.05   # 5% → block
_DAILY_LOSS_WARN = 0.03   # 3% → warning
_MAX_POSITIONS   = 2      # futures: max 2 concurrent (tighter than Forex 3)
_MAX_RISK_PCT    = 0.01   # 1% risk rule for futures
_MIN_CONTRACTS   = 0.001
_MAX_CONTRACTS   = 100.0

# BTC futures: pip_size = 1 USD, "pip_value_per_lot" = 1 (contracts × USD distance = risk USD)
_BTC_PIP_CONFIG = {"pip_size": 1.0, "pip_value_per_lot": 1.0}


@dataclass(frozen=True)
class RiskVerifierResult:
    """Wynik weryfikacji ryzyka — czysto deterministyczny."""

    risk_approved: bool
    position_size: float              # contracts (BTC)
    risk_notes: list[str] = field(default_factory=list)
    rejection_reason: str = ""
    spread_z_score: float | None = None
    portfolio_corr_blocked: bool = False
    circuit_breaker_hit: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class RiskVerifier:
    """Weryfikuje ryzyko sygnału — deterministyczny bloker.

    NIE używa LLM. Publiczna metoda: verify(input_data) → RiskVerifierResult.
    Nigdy nie rzuca wyjątku.
    """

    def __init__(self) -> None:
        self._log = structlog.get_logger().bind(agent="risk_verifier")

    def verify(self, input_data: dict[str, Any]) -> RiskVerifierResult:
        """Weryfikuje ryzyko sygnału. Nigdy nie rzuca wyjątku."""
        try:
            return self._verify_internal(input_data)
        except Exception as exc:
            self._log.error("risk_verifier_internal_error", error=str(exc))
            return RiskVerifierResult(
                risk_approved=False,
                position_size=0.0,
                risk_notes=[f"Internal error: {exc}"],
                rejection_reason=f"Internal error: {exc}",
                circuit_breaker_hit="",
                timestamp=datetime.now(timezone.utc),
            )

    # ── Internal logic ────────────────────────────────────────────────────────

    def _verify_internal(self, input_data: dict[str, Any]) -> RiskVerifierResult:
        instrument: str     = input_data.get("instrument", "BTC_USD")
        direction: str      = input_data.get("direction", "")
        entry: float        = float(input_data.get("entry", 0.0))
        stop_loss: float    = float(input_data.get("stop_loss", 0.0))
        position_size_contracts: float = float(input_data.get("position_size_lots", 0.001))
        account_balance: float  = float(input_data.get("account_balance", 1000.0))
        current_spread: float | None   = input_data.get("current_spread")
        spread_history: list[float] | None = input_data.get("spread_history")
        open_positions: list[dict]     = input_data.get("open_positions", [])
        daily_loss_pct: float          = float(input_data.get("daily_loss_pct", 0.0))
        confluence_score: int          = int(input_data.get("confluence_score", 0))

        risk_notes: list[str]  = []
        circuit_breaker_hit    = ""
        portfolio_corr_blocked = False
        risk_approved          = True
        rejection_reason       = ""

        # ── 1. Daily loss circuit breaker ────────────────────────────────────
        if daily_loss_pct >= _DAILY_LOSS_HARD:
            circuit_breaker_hit = "daily_loss"
            risk_approved       = False
            rejection_reason    = "Daily loss limit reached (≥5%)"
            self._log.warning(
                "circuit_breaker_daily_loss_hard",
                daily_loss_pct=daily_loss_pct,
                instrument=instrument,
            )
        elif daily_loss_pct >= _DAILY_LOSS_WARN:
            risk_notes.append(
                f"WARNING: Daily loss at {daily_loss_pct * 100:.1f}%, approaching 5% limit"
            )

        # ── 2. Max concurrent positions ──────────────────────────────────────
        if risk_approved and len(open_positions) >= _MAX_POSITIONS:
            circuit_breaker_hit = "max_positions"
            risk_approved       = False
            rejection_reason    = f"Maximum {_MAX_POSITIONS} concurrent positions reached"
            self._log.warning(
                "circuit_breaker_max_positions",
                open_count=len(open_positions),
            )

        # ── 3. Duplicate BTC position (same direction) ───────────────────────
        if risk_approved:
            dup = self._check_duplicate(instrument, direction, open_positions)
            if dup:
                portfolio_corr_blocked = True
                risk_approved          = False
                rejection_reason       = dup

        # ── 4. Spread z-score (informational) ───────────────────────────────
        spread_z = self._calculate_spread_zscore(current_spread, spread_history, risk_notes)

        # ── 5. Position sizing validation (1% risk rule) ─────────────────────
        position_size_contracts = self._validate_sizing(
            position_size_contracts, entry, stop_loss, account_balance, risk_notes
        )

        self._log.info(
            "risk_verification_complete",
            instrument=instrument,
            direction=direction,
            risk_approved=risk_approved,
            circuit_breaker_hit=circuit_breaker_hit or None,
            position_size=position_size_contracts,
            confluence_score=confluence_score,
        )

        return RiskVerifierResult(
            risk_approved=risk_approved,
            position_size=position_size_contracts,
            risk_notes=risk_notes,
            rejection_reason=rejection_reason,
            spread_z_score=spread_z,
            portfolio_corr_blocked=portfolio_corr_blocked,
            circuit_breaker_hit=circuit_breaker_hit,
            timestamp=datetime.now(timezone.utc),
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _check_duplicate(
        self,
        instrument: str,
        direction: str,
        open_positions: list[dict],
    ) -> str | None:
        """Block duplicate (same instrument + same direction) positions."""
        for pos in open_positions:
            if (
                str(pos.get("instrument", "")) == instrument
                and str(pos.get("direction", "")) == direction
            ):
                return f"Duplicate: {instrument} {direction} already open"
        return None

    def _calculate_spread_zscore(
        self,
        current_spread: float | None,
        spread_history: list[float] | None,
        risk_notes: list[str],
    ) -> float | None:
        if spread_history is None or len(spread_history) < 5 or current_spread is None:
            return None
        mean = statistics.mean(spread_history)
        try:
            std = statistics.stdev(spread_history)
        except statistics.StatisticsError:
            return None
        z = (current_spread - mean) / std if std > 0 else 0.0
        if z > 2.0:
            risk_notes.append(f"WARNING: Spread z-score {z:.2f} — elevated spread")
        return z

    def _validate_sizing(
        self,
        contracts: float,
        entry: float,
        stop_loss: float,
        account_balance: float,
        risk_notes: list[str],
    ) -> float:
        """Scale down contracts if they would breach the 1% risk rule."""
        sl_distance = abs(entry - stop_loss)
        if sl_distance <= 0 or account_balance <= 0:
            return contracts

        # For BTC futures: risk_usd = contracts * sl_distance
        risk_usd = contracts * sl_distance
        risk_pct = risk_usd / account_balance

        if risk_pct > _MAX_RISK_PCT:
            max_risk_usd   = account_balance * _MAX_RISK_PCT
            new_contracts  = max_risk_usd / sl_distance
            new_contracts  = max(_MIN_CONTRACTS, min(_MAX_CONTRACTS, round(new_contracts, 4)))
            risk_notes.append(
                f"ALERT: Position exceeds 1% risk rule — "
                f"scaled from {contracts:.4f} to {new_contracts:.4f} BTC"
            )
            self._log.info(
                "sizing_scaled_down",
                original_contracts=contracts,
                new_contracts=new_contracts,
                risk_pct=risk_pct,
            )
            return new_contracts

        return contracts
