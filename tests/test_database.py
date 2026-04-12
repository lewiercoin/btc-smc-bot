"""Tests for db/database.py."""

from __future__ import annotations

import pytest

from db.database import Database


@pytest.fixture
def db() -> Database:
    """In-memory database for tests."""
    d = Database(db_path=":memory:")
    d.initialize()
    return d


class TestDatabase:
    def test_initialize_creates_tables(self, db):
        cursor = db.conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        assert "signals" in tables
        assert "positions" in tables
        assert "candles" in tables

    def test_save_and_retrieve_signal(self, db):
        payload = {
            "signal_uuid":      "test-uuid-001",
            "instrument":       "BTC_USD",
            "direction":        "bullish",
            "entry_price":      50000.0,
            "sl_price":         49500.0,
            "tp1_price":        50750.0,
            "tp2_price":        51250.0,
            "tp3_price":        52750.0,
            "confluence_score": 72,
            "leverage":         3,
            "position_size":    0.002,
            "liquidation_price": 33333.0,
            "session":          "H1",
            "status":           "OPEN",
            "created_at":       "2025-01-01T12:00:00+00:00",
        }
        db.save_signal(payload)
        open_signals = db.get_open_signals()
        assert len(open_signals) == 1
        assert open_signals[0]["instrument"] == "BTC_USD"
        assert open_signals[0]["direction"] == "bullish"

    def test_duplicate_signal_uuid_ignored(self, db):
        payload = {
            "signal_uuid": "dup-001",
            "instrument": "BTC_USD", "direction": "bearish",
            "entry_price": 50000.0, "sl_price": 50500.0,
            "tp1_price": 49250.0, "tp2_price": 48750.0, "tp3_price": 47250.0,
            "confluence_score": 68, "leverage": 3, "position_size": 0.001,
            "liquidation_price": 66666.0, "session": "H1",
            "status": "OPEN", "created_at": "2025-01-01T12:00:00+00:00",
        }
        db.save_signal(payload)
        db.save_signal(payload)  # second insert should be ignored
        signals = db.get_open_signals()
        assert len(signals) == 1

    def test_update_signal_status(self, db):
        payload = {
            "signal_uuid": "upd-001", "instrument": "BTC_USD", "direction": "bullish",
            "entry_price": 50000.0, "sl_price": 49500.0,
            "tp1_price": 50750.0, "tp2_price": 51250.0, "tp3_price": 52750.0,
            "confluence_score": 70, "leverage": 3, "position_size": 0.002,
            "liquidation_price": 33333.0, "session": "H1",
            "status": "OPEN", "created_at": "2025-01-01T12:00:00+00:00",
        }
        db.save_signal(payload)
        db.update_signal_status("upd-001", "CLOSED", exit_price=51000.0, pnl_r=1.5)

        cursor = db.conn.cursor()
        cursor.execute("SELECT status, exit_price, pnl_r FROM signals WHERE signal_uuid='upd-001'")
        row = cursor.fetchone()
        assert row["status"] == "CLOSED"
        assert row["exit_price"] == pytest.approx(51000.0)
        assert row["pnl_r"] == pytest.approx(1.5)

    def test_get_open_signals_empty_initially(self, db):
        assert db.get_open_signals() == []

    def test_insert_position(self, db):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        db.insert_position(
            position_id="pos-001",
            signal_id="sig-001",
            symbol="BTCUSDT",
            direction="LONG",
            status="OPEN",
            entry_price=50000.0,
            size=0.002,
            leverage=3,
            stop_loss=49500.0,
            take_profit_1=50750.0,
            take_profit_2=51250.0,
            opened_at=now,
            updated_at=now,
        )
        positions = db.get_open_positions()
        assert len(positions) == 1
        assert positions[0]["direction"] == "LONG"
