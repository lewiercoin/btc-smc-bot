"""Paper execution engine for btc-smc-bot.

Simulates order fills without real Binance API calls.
Used during paper trading phase before going live.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from execution.execution_engine import ExecutionEngine, PositionPersister


class PaperExecutionEngine(ExecutionEngine):
    def __init__(self, *, position_persister: PositionPersister, symbol: str = "BTCUSDT") -> None:
        self.position_persister = position_persister
        self.symbol = symbol.upper()

    def execute_signal(
        self,
        signal_id:     str,
        direction:     str,
        entry_price:   float,
        stop_loss:     float,
        take_profit_1: float,
        take_profit_2: float,
        size:          float,
        leverage:      int,
    ) -> None:
        """Simulate position open — no real API calls."""
        position_id = f"paper-{uuid4().hex}"
        now = datetime.now(timezone.utc)

        self.position_persister.insert_position(
            position_id=position_id,
            signal_id=signal_id,
            symbol=self.symbol,
            direction=direction,
            status="OPEN",
            entry_price=entry_price,
            size=size,
            leverage=leverage,
            stop_loss=stop_loss,
            take_profit_1=take_profit_1,
            take_profit_2=take_profit_2,
            opened_at=now,
            updated_at=now,
        )
        self.position_persister.commit()
