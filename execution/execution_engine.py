"""Execution engine base + PositionPersister protocol.

Adapted from btc-bot — uses Signal from engine.signal_generator instead of ExecutableSignal.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Protocol

from execution.execution_types import FillEvent


class PositionPersister(Protocol):
    def insert_position(
        self,
        *,
        position_id:   str,
        signal_id:     str,
        symbol:        str,
        direction:     str,
        status:        str,
        entry_price:   float,
        size:          float,
        leverage:      int,
        stop_loss:     float,
        take_profit_1: float,
        take_profit_2: float,
        opened_at:     datetime,
        updated_at:    datetime,
    ) -> None: ...

    def insert_execution_fill_event(
        self,
        *,
        position_id: str,
        order_type:  str,
        fill_event:  FillEvent,
    ) -> None: ...

    def commit(self) -> None: ...


class ExecutionEngine(ABC):
    """Execution layer. No strategic filtering or risk gating here."""

    @abstractmethod
    def execute_signal(self, signal_id: str, direction: str, entry_price: float,
                       stop_loss: float, take_profit_1: float, take_profit_2: float,
                       size: float, leverage: int) -> None:
        raise NotImplementedError
