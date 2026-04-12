"""SQLite database for btc-smc-bot.

Schema merges smc-signal-bot signals table with btc-bot futures columns.
"""

from __future__ import annotations

import sqlite3
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class Database:
    """SQLite handler for btc-smc-bot."""

    def __init__(self, db_path: str = "btc_smc.db") -> None:
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.logger = logger.bind(module="database", db_path=db_path)
        self.logger.info("database_initialized")

    def initialize(self) -> None:
        """Create tables if they do not exist."""
        cursor = self.conn.cursor()

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS signals (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_uuid       TEXT UNIQUE,
                instrument        TEXT NOT NULL DEFAULT 'BTC_USD',
                direction         TEXT NOT NULL,
                entry_price       REAL,
                sl_price          REAL,
                tp1_price         REAL,
                tp2_price         REAL,
                tp3_price         REAL,
                confluence_score  INTEGER,
                leverage          INTEGER DEFAULT 3,
                position_size     REAL,
                liquidation_price REAL,
                session           TEXT,
                created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status            TEXT DEFAULT 'OPEN',
                closed_at         TIMESTAMP,
                exit_price        REAL,
                pnl_r             REAL,
                pnl_usd           REAL,
                exit_reason       TEXT
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS positions (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                position_id   TEXT UNIQUE,
                signal_id     TEXT,
                symbol        TEXT NOT NULL DEFAULT 'BTCUSDT',
                direction     TEXT NOT NULL,
                status        TEXT NOT NULL DEFAULT 'OPEN',
                entry_price   REAL,
                size          REAL,
                leverage      INTEGER,
                stop_loss     REAL,
                take_profit_1 REAL,
                take_profit_2 REAL,
                margin_used   REAL,
                liq_price     REAL,
                opened_at     TIMESTAMP,
                updated_at    TIMESTAMP,
                closed_at     TIMESTAMP,
                exit_price    REAL,
                pnl_usd       REAL,
                pnl_r         REAL,
                exit_reason   TEXT
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS candles (
                instrument  TEXT NOT NULL,
                granularity TEXT NOT NULL,
                time        TIMESTAMP NOT NULL,
                open        REAL NOT NULL,
                high        REAL NOT NULL,
                low         REAL NOT NULL,
                close       REAL NOT NULL,
                volume      REAL,
                PRIMARY KEY (instrument, granularity, time)
            )
            """
        )

        self.conn.commit()
        self.logger.info("tables_initialized")

    # ── Signals ───────────────────────────────────────────────────────────────

    def save_signal(self, payload: dict[str, Any]) -> None:
        """Insert a new signal record."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT OR IGNORE INTO signals
                (signal_uuid, instrument, direction, entry_price, sl_price,
                 tp1_price, tp2_price, tp3_price, confluence_score,
                 leverage, position_size, liquidation_price, session, status, created_at)
            VALUES
                (:signal_uuid, :instrument, :direction, :entry_price, :sl_price,
                 :tp1_price, :tp2_price, :tp3_price, :confluence_score,
                 :leverage, :position_size, :liquidation_price, :session, :status, :created_at)
            """,
            payload,
        )
        self.conn.commit()

    def get_open_signals(self) -> list[dict[str, Any]]:
        """Return all OPEN signals as list of dicts."""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM signals WHERE status = 'OPEN' ORDER BY created_at DESC"
        )
        rows = cursor.fetchall()
        return [
            {
                "id":        row["signal_uuid"],
                "instrument": row["instrument"],
                "direction":  row["direction"],
                "entry":      row["entry_price"],
                "status":     row["status"],
            }
            for row in rows
        ]

    def update_signal_status(
        self,
        signal_uuid: str,
        status: str,
        exit_price: float | None = None,
        pnl_r: float | None = None,
        pnl_usd: float | None = None,
        exit_reason: str | None = None,
    ) -> None:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            UPDATE signals
            SET status=:status, exit_price=:exit_price, pnl_r=:pnl_r,
                pnl_usd=:pnl_usd, exit_reason=:exit_reason, closed_at=datetime('now')
            WHERE signal_uuid=:uuid
            """,
            {
                "uuid":       signal_uuid,
                "status":     status,
                "exit_price": exit_price,
                "pnl_r":      pnl_r,
                "pnl_usd":    pnl_usd,
                "exit_reason": exit_reason,
            },
        )
        self.conn.commit()

    # ── Positions ─────────────────────────────────────────────────────────────

    def insert_position(self, **kwargs: Any) -> None:
        """Insert a new futures position record."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT OR IGNORE INTO positions
                (position_id, signal_id, symbol, direction, status,
                 entry_price, size, leverage, stop_loss,
                 take_profit_1, take_profit_2, opened_at, updated_at)
            VALUES
                (:position_id, :signal_id, :symbol, :direction, :status,
                 :entry_price, :size, :leverage, :stop_loss,
                 :take_profit_1, :take_profit_2, :opened_at, :updated_at)
            """,
            kwargs,
        )
        self.conn.commit()

    def get_open_positions(self) -> list[dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM positions WHERE status IN ('OPEN', 'PARTIAL') ORDER BY opened_at DESC"
        )
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def commit(self) -> None:
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()
