"""APScheduler — BTC H1 scan co 15 minut.

Faza 1: tylko jeden job — scan_btc_h1.
Faza 2: dodać weekly optimizer job (po weryfikacji sygnałów).
"""

from __future__ import annotations

import logging
from typing import Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)


class SignalScheduler:
    """Runs signal scanning on a fixed interval using APScheduler."""

    def __init__(
        self,
        scan_fn:        Callable[[], None],
        interval_minutes: int = 15,
    ) -> None:
        """
        Args:
            scan_fn: Callable invoked every interval_minutes. Should call
                     SignalGenerator.generate() and publish result.
            interval_minutes: Scan frequency in minutes (default 15).
        """
        self._scan_fn         = scan_fn
        self._interval_minutes = interval_minutes
        self._scheduler       = BackgroundScheduler(timezone="UTC")

    def start(self) -> None:
        """Add jobs and start the scheduler."""
        self._scheduler.add_job(
            func=self._safe_scan,
            trigger=IntervalTrigger(minutes=self._interval_minutes),
            id="scan_btc_h1",
            name="BTC/USDT H1 Signal Scan",
            replace_existing=True,
            misfire_grace_time=60,
        )
        self._scheduler.start()
        logger.info(
            "scheduler_started",
            extra={"interval_minutes": self._interval_minutes},
        )

    def stop(self) -> None:
        """Gracefully shut down the scheduler."""
        self._scheduler.shutdown(wait=False)
        logger.info("scheduler_stopped")

    def _safe_scan(self) -> None:
        """Wrap scan_fn; prevent one failure from killing the scheduler."""
        try:
            self._scan_fn()
        except Exception as exc:
            logger.error("scan_error", exc_info=exc)
