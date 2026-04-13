"""btc-smc-bot — entry point.

Modes:
  python main.py --mode paper    # paper trading (default)
  python main.py --mode live     # live trading (requires Binance API keys)
  python main.py --mode signal   # generate one signal and exit (debugging)

Environment:
  .env file with BINANCE_API_KEY, BINANCE_API_SECRET, TELEGRAM_BOT_TOKEN, etc.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time

import structlog
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = structlog.get_logger(__name__)


def _setup_signal_generator(mode: str):
    from connectors.binance_client import BinanceClient
    from connectors.news_client import NewsClient
    from db.database import Database
    from engine.smc_signal_generator import SignalGenerator

    db = Database(db_path=os.getenv("DB_PATH", "btc_smc.db"))
    db.initialize()

    gen = SignalGenerator(
        binance_client=BinanceClient(),
        news_client=NewsClient(),
        db=db,
        confluence_threshold=int(os.getenv("CONFLUENCE_THRESHOLD", "65")),
        max_risk_pct=float(os.getenv("MAX_RISK_PCT", "0.01")),
        max_leverage=int(os.getenv("MAX_LEVERAGE", "3")),
    )
    return gen, db


def run_paper(args: argparse.Namespace) -> None:
    """Paper trading mode — generates signals and simulates execution."""
    from bot.scheduler import SignalScheduler
    from connectors.rest_client import BinanceFuturesRestClient, RestClientConfig
    from db.database import Database
    from execution.paper_execution_engine import PaperExecutionEngine

    gen, db = _setup_signal_generator("paper")
    logger.info("btc_smc_bot_started", mode="paper")

    class _DBPositionPersister:
        def insert_position(self, **kwargs) -> None:
            db.insert_position(**kwargs)
        def insert_execution_fill_event(self, **kwargs) -> None:
            pass  # paper: no fill events needed
        def commit(self) -> None:
            db.commit()

    exec_engine = PaperExecutionEngine(position_persister=_DBPositionPersister())

    def scan_and_execute() -> None:
        signal = gen.generate()
        if signal is None:
            return
        logger.info(
            "signal_generated",
            direction=signal.direction,
            score=signal.confluence_score,
            entry=signal.entry,
            sl=signal.stop_loss,
            contracts=signal.position_size,
        )
        direction_upper = "LONG" if signal.direction == "bullish" else "SHORT"
        exec_engine.execute_signal(
            signal_id=signal.id,
            direction=direction_upper,
            entry_price=signal.entry,
            stop_loss=signal.stop_loss,
            take_profit_1=signal.take_profit_1,
            take_profit_2=signal.take_profit_2,
            size=signal.position_size,
            leverage=signal.leverage,
        )

    scheduler = SignalScheduler(
        scan_fn=scan_and_execute,
        interval_minutes=int(os.getenv("SCAN_INTERVAL_MINUTES", "15")),
    )
    scheduler.start()

    def _shutdown(signum, frame):
        logger.info("shutting_down")
        scheduler.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    logger.info("paper_trading_running", hint="Ctrl+C to stop")
    while True:
        time.sleep(60)


def run_live(args: argparse.Namespace) -> None:
    """Live trading mode — requires verified paper trading results first."""
    from bot.scheduler import SignalScheduler
    from connectors.rest_client import BinanceFuturesRestClient, RestClientConfig
    from db.database import Database
    from execution.live_execution_engine import LiveExecutionEngine
    from monitoring.audit_logger import AuditLogger

    gen, db = _setup_signal_generator("live")

    config = RestClientConfig(
        base_url="https://fapi.binance.com",
        timeout_seconds=10,
        api_key=os.getenv("BINANCE_API_KEY", ""),
        api_secret=os.getenv("BINANCE_API_SECRET", ""),
    )
    rest_client = BinanceFuturesRestClient(config=config)
    audit_logger = AuditLogger(connection=db.conn)

    class _DBPositionPersister:
        def insert_position(self, **kwargs) -> None:
            db.insert_position(**kwargs)
        def insert_execution_fill_event(self, **kwargs) -> None:
            pass
        def commit(self) -> None:
            db.commit()

    exec_engine = LiveExecutionEngine(
        position_persister=_DBPositionPersister(),
        rest_client=rest_client,
        audit_logger=audit_logger,
    )

    def scan_and_execute() -> None:
        sig = gen.generate()
        if sig is None:
            return
        direction_upper = "LONG" if sig.direction == "bullish" else "SHORT"
        exec_engine.execute_signal(
            signal_id=sig.id,
            direction=direction_upper,
            entry_price=sig.entry,
            stop_loss=sig.stop_loss,
            take_profit_1=sig.take_profit_1,
            take_profit_2=sig.take_profit_2,
            size=sig.position_size,
            leverage=sig.leverage,
        )

    scheduler = SignalScheduler(scan_fn=scan_and_execute)
    scheduler.start()
    logger.info("btc_smc_bot_started", mode="live")

    def _shutdown(signum, frame):
        scheduler.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    while True:
        time.sleep(60)


def run_signal(args: argparse.Namespace) -> None:
    """One-shot signal generation for debugging."""
    gen, _ = _setup_signal_generator("signal")
    result = gen.generate()
    if result:
        print(f"Signal generated: {result.direction} | entry={result.entry} "
              f"| SL={result.stop_loss} | score={result.confluence_score}")
    else:
        print("No signal generated.")


def main() -> None:
    parser = argparse.ArgumentParser(description="btc-smc-bot")
    parser.add_argument("--mode", choices=["paper", "live", "signal"], default="paper")
    args = parser.parse_args()

    if args.mode == "paper":
        run_paper(args)
    elif args.mode == "live":
        run_live(args)
    elif args.mode == "signal":
        run_signal(args)


if __name__ == "__main__":
    main()
