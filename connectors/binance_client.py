"""Binance Futures client for btc-smc-bot.

High-level wrapper around BinanceFuturesRestClient that exposes the same
interface as OandaClient in smc-signal-bot.  All SMC components consume
the shared `Candle` dataclass defined here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import structlog
from dotenv import load_dotenv

from connectors.rest_client import BinanceFuturesRestClient, RestClientConfig

load_dotenv()

logger = structlog.get_logger(__name__)

# ── Candle — universal OHLCV type used across the whole bot ──────────────────

@dataclass(frozen=True)
class Candle:
    """OHLCV candle — identical contract to smc-signal-bot Candle.

    instrument: internal key, always "BTC_USD".
    volume: Binance base-asset volume (BTC), same proxy role as OANDA tick volume.
    """

    instrument: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int  # cast from float; sufficient for SMC volume comparisons


# ── Interval / pair mappings ─────────────────────────────────────────────────

_TIMEFRAME_MAP: dict[str, str] = {
    "M15": "15m",
    "H1":  "1h",
    "H4":  "4h",
    "D1":  "1d",
}

_PAIR_MAP: dict[str, str] = {
    "BTC_USD": "BTCUSDT",
    "BTCUSDT": "BTCUSDT",
}

_LIVE_BASE_URL = "https://fapi.binance.com"
_TESTNET_BASE_URL = "https://testnet.binancefuture.com"


# ── BinanceClient ─────────────────────────────────────────────────────────────


class BinanceClient:
    """High-level Binance Futures client for btc-smc-bot.

    Provides the same interface as OandaClient:
        get_candles(instrument, granularity, count) → list[Candle]
        get_current_spread(instrument) → float
        get_futures_balance() → float

    Environment variables:
        BINANCE_API_KEY      — required for signed requests (balance, orders)
        BINANCE_API_SECRET   — required for signed requests
        BINANCE_TESTNET      — "1" or "true" to use testnet (default: live)
    """

    def __init__(self) -> None:
        api_key = os.getenv("BINANCE_API_KEY", "")
        api_secret = os.getenv("BINANCE_API_SECRET", "")
        testnet = os.getenv("BINANCE_TESTNET", "0").lower() in {"1", "true", "yes"}

        base_url = _TESTNET_BASE_URL if testnet else _LIVE_BASE_URL

        config = RestClientConfig(
            base_url=base_url,
            timeout_seconds=10,
            max_retries=3,
            retry_backoff_seconds=0.75,
            api_key=api_key,
            api_secret=api_secret,
        )
        self._client = BinanceFuturesRestClient(config=config)
        self._testnet = testnet
        self.logger = logger.bind(module="binance_client", testnet=testnet)
        self.logger.info("binance_client_initialized")

    # ── Public interface ──────────────────────────────────────────────────────

    def get_candles(
        self,
        instrument: str,
        granularity: str,
        count: int,
    ) -> list[Candle]:
        """Fetch OHLCV candles from Binance Futures.

        Args:
            instrument: Internal key, e.g. "BTC_USD".
            granularity: Timeframe string, e.g. "H1" or "H4".
            count: Number of closed candles to fetch (max 1500).

        Returns:
            List of Candle objects, chronological order (oldest first).
        """
        symbol = _PAIR_MAP.get(instrument.upper(), instrument.upper())
        interval = _TIMEFRAME_MAP.get(granularity, granularity.lower())

        # Fetch count+1 and drop the last (still-open) candle
        raw = self._client.fetch_klines(symbol=symbol, interval=interval, limit=count + 1)
        # Drop last (current incomplete candle) if we got it
        if len(raw) > count:
            raw = raw[:-1]

        candles = [self._to_candle(k, instrument) for k in raw]
        self.logger.debug(
            "candles_fetched",
            instrument=instrument,
            granularity=granularity,
            count=len(candles),
        )
        return candles

    def get_current_spread(self, instrument: str) -> float:
        """Return current bid-ask spread in USD (ask - bid).

        Args:
            instrument: Internal key, e.g. "BTC_USD".

        Returns:
            Spread in USD.  Returns 0.0 on failure.
        """
        symbol = _PAIR_MAP.get(instrument.upper(), instrument.upper())
        try:
            ticker = self._client.fetch_book_ticker(symbol)
            spread = ticker["ask"] - ticker["bid"]
            self.logger.debug("spread_fetched", instrument=instrument, spread=spread)
            return spread
        except Exception as exc:
            self.logger.warning("spread_fetch_failed", instrument=instrument, error=str(exc))
            return 0.0

    def get_futures_balance(self) -> float:
        """Return available USDT balance on Futures account.

        Returns:
            USDT balance as float.  Returns 0.0 on failure.
        """
        try:
            payload = self._client.signed_request("/fapi/v2/balance")
            if not isinstance(payload, list):
                return 0.0
            for item in payload:
                if str(item.get("asset", "")).upper() == "USDT":
                    return float(item.get("availableBalance", 0.0))
            return 0.0
        except Exception as exc:
            self.logger.warning("balance_fetch_failed", error=str(exc))
            return 0.0

    def ping(self) -> bool:
        """Check connectivity to Binance Futures API."""
        return self._client.ping()

    # ── Private helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _to_candle(raw: dict[str, Any], instrument: str) -> Candle:
        """Convert a normalized kline dict to Candle dataclass."""
        return Candle(
            instrument=instrument,
            timestamp=raw["open_time"],
            open=float(raw["open"]),
            high=float(raw["high"]),
            low=float(raw["low"]),
            close=float(raw["close"]),
            volume=int(float(raw["volume"])),
        )
