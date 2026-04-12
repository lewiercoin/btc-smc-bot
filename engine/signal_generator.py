"""Signal Generator — główny orchestrator pipeline'u btc-smc-bot.

Pipeline fazy 1 (deterministyczny, bez agentów LLM):

  Binance candles → Data Quality → Confluence Scorer
  → Risk Engine → Risk Verifier → Signal → DB

Agenci AI (structure_agent, fundamental_agent) zostaną dodani w fazie 2
po weryfikacji sygnałów bazowych w paper tradingu (≥10 sygnałów, ≥2 tyg).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import structlog

from agents.risk_verifier import RiskVerifier, RiskVerifierResult
from db.database import Database
from dq.data_quality import DataQualityChecker
from engine.confluence_scorer import ConfluenceScorer
from engine.risk_engine import RiskEngine

if TYPE_CHECKING:
    from connectors.binance_client import BinanceClient, Candle
    from connectors.news_client import NewsClient
    from engine.confluence_scorer import ConfluenceResult
    from engine.risk_engine import TradeParameters

logger = structlog.get_logger(__name__)

SYMBOL        = "BTC_USD"
BINANCE_SYMBOL = "BTCUSDT"

_HTF_TIMEFRAME = "H4"
_LTF_COUNT     = 100
_HTF_COUNT     = 50


# ── Signal dataclass ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Signal:
    """Fully computed BTC futures trade signal.

    Compatible with smc-signal-bot Signal interface.
    Extensions: leverage, contracts (actual Binance units).
    """

    # Identification
    id: str
    pair: str          # always "BTC_USD"
    timeframe: str
    direction: str     # "bullish" | "bearish"
    timestamp: datetime

    # Trade parameters
    entry: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    take_profit_3: float
    position_size: float    # BTC contracts
    risk_reward_ratio: float
    leverage: int
    liquidation_price: float

    # Confluence
    confluence_score: int      # 0–110
    confluence_components: tuple[Any, ...]

    # Risk info
    risk_amount: float
    risk_pct: float
    sl_distance: float
    atr_at_entry: float

    # Lifecycle
    status: str = "pending"
    notes: str  = ""


# ── SignalGenerator ───────────────────────────────────────────────────────────


class SignalGenerator:
    """Pipeline orchestrator — produces BTC/USDT futures Signals.

    Faza 1: deterministyczny (brak agentów LLM).
    Wszystkie zewnętrzne błędy (Binance timeout, DQ fail) zwracają None.
    """

    def __init__(
        self,
        binance_client: BinanceClient | None = None,
        news_client: NewsClient | None = None,
        db: Database | None = None,
        confluence_threshold: int = 65,
        max_risk_pct: float = 0.01,
        max_leverage: int = 3,
    ) -> None:
        if binance_client is None:
            from connectors.binance_client import BinanceClient as _BC  # noqa: PLC0415
            binance_client = _BC()
        if news_client is None:
            from connectors.news_client import NewsClient as _NC  # noqa: PLC0415
            news_client = _NC()
        if db is None:
            db = Database()

        self.binance    = binance_client
        self.news       = news_client
        self.db         = db
        self.dq         = DataQualityChecker()
        self.scorer     = ConfluenceScorer()
        self.risk       = RiskEngine(max_risk_pct=max_risk_pct, max_leverage=max_leverage)
        self.risk_verifier = RiskVerifier()
        self.confluence_threshold = confluence_threshold
        self.logger = logger.bind(module="signal_generator")

    # ── Public interface ──────────────────────────────────────────────────────

    def generate(
        self,
        pair: str = SYMBOL,
        timeframe: str = "H1",
        account_balance: float | None = None,
    ) -> Signal | None:
        """Run the full pipeline for BTC/USDT.

        Returns:
            Signal if all gates pass, None otherwise.
        """
        log = self.logger.bind(pair=pair, timeframe=timeframe)
        log.info("pipeline_start")

        # ── STEP 1: Fetch data ────────────────────────────────────────────────
        fetch = self._fetch_data(pair, timeframe)
        if fetch is None:
            log.info("pipeline_abort", step="fetch")
            return None
        candles, htf_candles, spread = fetch

        # ── STEP 2: Data Quality gate ─────────────────────────────────────────
        dq_ok, dq_reason = self._run_dq_checks(pair, candles, spread)
        if not dq_ok:
            log.info("pipeline_abort", step="data_quality", reason=dq_reason)
            return None

        # ── STEP 3: Confluence scoring gate ───────────────────────────────────
        try:
            confluence = self.scorer.score(candles, htf_candles, pair)
        except Exception as exc:
            log.error("confluence_scoring_error", error=str(exc))
            return None

        if confluence.total_score < self.confluence_threshold:
            log.info(
                "pipeline_abort",
                step="confluence",
                reason=f"score {confluence.total_score} < {self.confluence_threshold}",
            )
            return None

        if confluence.setup_direction == "neutral":
            log.info("pipeline_abort", step="confluence", reason="neutral direction")
            return None

        log.info(
            "confluence_gate_passed",
            score=confluence.total_score,
            direction=confluence.setup_direction,
        )

        # ── STEP 3.5: Risk Verifier (deterministic blocker) ───────────────────
        balance = account_balance or self._get_futures_balance()
        rv_ok, rv_reason = self._run_risk_verifier(
            pair=pair,
            confluence=confluence,
            candles=candles,
            spread=spread,
            account_balance=balance,
        )
        if not rv_ok:
            log.info("pipeline_abort", step="risk_verifier", reason=rv_reason)
            return None

        # ── STEP 4: Risk Engine ────────────────────────────────────────────────
        try:
            trade = self.risk.calculate_trade(
                candles=candles,
                setup_direction=confluence.setup_direction,
                pair=pair,
                account_balance=balance,
                current_spread=spread,
            )
        except Exception as exc:
            log.error("risk_calculation_error", error=str(exc))
            return None

        if trade is None or not trade.is_valid:
            log.info(
                "pipeline_abort",
                step="risk",
                reason=trade.rejection_reason if trade else "None",
            )
            return None

        log.info(
            "risk_gate_passed",
            contracts=trade.position_size.lots,
            rr=trade.risk_reward_ratio,
            sl=trade.stop_loss,
            liq=trade.liquidation_price,
        )

        # ── STEP 5: Build Signal and persist ──────────────────────────────────
        signal = self._build_signal(pair, timeframe, confluence, trade)
        self._save_signal(signal)

        log.info(
            "pipeline_complete",
            signal_id=signal.id,
            entry=signal.entry,
            sl=signal.stop_loss,
            score=signal.confluence_score,
        )

        return signal

    # ── Private helpers ───────────────────────────────────────────────────────

    def _fetch_data(
        self,
        pair: str,
        timeframe: str,
    ) -> tuple[list[Candle], list[Candle], float | None] | None:
        try:
            candles     = self.binance.get_candles(pair, timeframe, count=_LTF_COUNT)
            htf_candles = self.binance.get_candles(pair, _HTF_TIMEFRAME, count=_HTF_COUNT)
            try:
                spread = self.binance.get_current_spread(pair)
            except Exception as spread_exc:
                self.logger.warning("spread_fetch_failed", error=str(spread_exc))
                spread = None
            return candles, htf_candles, spread
        except Exception as exc:
            self.logger.error("fetch_failed", pair=pair, error=str(exc))
            return None

    def _run_dq_checks(
        self,
        pair: str,
        candles: list[Candle],
        spread: float | None,
    ) -> tuple[bool, str]:
        dq_result = self.dq.check_candles(candles)
        if not dq_result.passed:
            return False, f"Candle DQ failed: {'; '.join(dq_result.issues[:3])}"

        if spread is not None:
            if not self.dq.check_spread(spread, pair):
                return False, f"Spread DQ failed: ${spread:.2f}"

        try:
            news_result = self.news.is_news_blocked(pair=pair, window_minutes=120)
            if news_result.is_blocked:
                return False, f"News blackout: {news_result.reason}"
        except Exception as exc:
            self.logger.warning("news_check_error_fail_safe", error=str(exc))
            return False, f"News check error (fail-safe): {exc}"

        return True, ""

    def _run_risk_verifier(
        self,
        pair: str,
        confluence: ConfluenceResult,
        candles: list[Candle],
        spread: float | None,
        account_balance: float,
    ) -> tuple[bool, str]:
        """Run deterministic Risk Verifier gate.

        Only blocks on hard circuit breakers:
        - daily_loss >= 5%
        - max_positions (2) reached
        - duplicate BTC position (same direction)
        """
        current_price = candles[-1].close if candles else 0.0

        risk_ctx: dict[str, Any] = {
            "instrument":         pair,
            "direction":          confluence.setup_direction,
            "entry":              current_price,
            "stop_loss":          0.0,  # placeholder; full SL computed in RiskEngine next
            "current_spread":     spread or 0.0,
            "account_balance":    account_balance,
            "open_positions":     self._get_open_positions(),
            "daily_loss_pct":     self._get_daily_pnl_pct(),
            "confluence_score":   confluence.total_score,
            "position_size_lots": 0.001,  # minimum placeholder
        }

        result: RiskVerifierResult = self.risk_verifier.verify(risk_ctx)

        self.logger.info(
            "risk_verifier_complete",
            risk_approved=result.risk_approved,
            rejection_reason=result.rejection_reason or None,
        )

        if not result.risk_approved:
            return False, f"RiskVerifier blocked: {result.rejection_reason}"
        return True, ""

    def _get_futures_balance(self) -> float:
        try:
            return self.binance.get_futures_balance()
        except Exception:
            return 1000.0  # fallback for paper trading

    def _get_open_positions(self) -> list[dict]:
        if self.db is None:
            return []
        try:
            return self.db.get_open_signals()
        except Exception:
            return []

    def _get_daily_pnl_pct(self) -> float:
        """Return today's PnL % for circuit breaker.

        Returns 0.0 during paper trading — equity tracking in phase 2.
        """
        return 0.0

    def _build_signal(
        self,
        pair: str,
        timeframe: str,
        confluence: ConfluenceResult,
        trade: TradeParameters,
    ) -> Signal:
        return Signal(
            id=str(uuid.uuid4()),
            pair=pair,
            timeframe=timeframe,
            direction=confluence.setup_direction,
            timestamp=datetime.now(tz=timezone.utc),
            entry=trade.entry,
            stop_loss=trade.stop_loss,
            take_profit_1=trade.take_profits.tp1,
            take_profit_2=trade.take_profits.tp2,
            take_profit_3=trade.take_profits.tp3,
            position_size=trade.position_size.lots,
            risk_reward_ratio=trade.risk_reward_ratio,
            leverage=trade.position_size.leverage,
            liquidation_price=trade.liquidation_price,
            confluence_score=confluence.total_score,
            confluence_components=tuple(confluence.components),
            risk_amount=trade.position_size.risk_amount,
            risk_pct=trade.position_size.risk_pct,
            sl_distance=trade.sl_distance,
            atr_at_entry=trade.atr_at_entry,
            status="pending",
            notes="",
        )

    def _save_signal(self, signal: Signal) -> None:
        try:
            payload: dict[str, Any] = {
                "signal_uuid":      signal.id,
                "instrument":       signal.pair,
                "direction":        signal.direction,
                "entry_price":      signal.entry,
                "sl_price":         signal.stop_loss,
                "tp1_price":        signal.take_profit_1,
                "tp2_price":        signal.take_profit_2,
                "tp3_price":        signal.take_profit_3,
                "confluence_score": signal.confluence_score,
                "leverage":         signal.leverage,
                "position_size":    signal.position_size,
                "liquidation_price": signal.liquidation_price,
                "session":          signal.timeframe,
                "status":           signal.status.upper(),
                "created_at":       signal.timestamp.isoformat(),
            }
            self.db.save_signal(payload)
        except Exception as exc:
            self.logger.error(
                "db_save_failed",
                signal_id=signal.id,
                error=str(exc),
            )
