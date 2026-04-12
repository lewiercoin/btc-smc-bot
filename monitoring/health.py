from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from dataclasses import dataclass

from connectors.rest_client import BinanceFuturesRestClient


@dataclass(slots=True)
class HealthStatus:
    db_writable: bool
    exchange_reachable: bool

    @property
    def healthy(self) -> bool:
        return self.db_writable and self.exchange_reachable


class HealthMonitor:
    def __init__(
        self,
        *,
        connection: sqlite3.Connection,
        rest_client: BinanceFuturesRestClient,
    ) -> None:
        self.connection = connection
        self.rest_client = rest_client

    def check(self) -> HealthStatus:
        return HealthStatus(
            db_writable=self._check_db_writable(),
            exchange_reachable=self._check_exchange_reachable(),
        )

    def _check_db_writable(self) -> bool:
        try:
            ts = datetime.now(timezone.utc).isoformat()
            self.connection.execute(
                """
                CREATE TEMP TABLE IF NOT EXISTS health_probe (
                    id INTEGER PRIMARY KEY,
                    ts TEXT NOT NULL
                )
                """
            )
            self.connection.execute(
                "INSERT OR REPLACE INTO health_probe (id, ts) VALUES (1, ?)",
                (ts,),
            )
            row = self.connection.execute("SELECT ts FROM health_probe WHERE id = 1").fetchone()
            return bool(row and row["ts"] == ts)
        except Exception:
            return False

    def _check_exchange_reachable(self) -> bool:
        try:
            return bool(self.rest_client.ping())
        except Exception:
            return False
