"""Execution types for btc-smc-bot.

Copied from btc-bot/core/execution_types.py — no changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Literal

OrderSide    = Literal["BUY", "SELL"]
OrderType    = Literal["LIMIT", "MARKET", "STOP_MARKET", "TAKE_PROFIT_MARKET"]
TimeInForce  = Literal["GTC", "IOC", "FOK"]


class ExecutionStatus(str, Enum):
    NEW              = "new"
    PARTIALLY_FILLED = "partially_filled"
    FILLED           = "filled"
    CANCELED         = "canceled"
    REJECTED         = "rejected"


@dataclass(slots=True)
class OrderRequest:
    client_order_id: str
    symbol:          str
    side:            OrderSide
    order_type:      OrderType
    qty:             float
    price:           float | None = None
    stop_price:      float | None = None
    time_in_force:   TimeInForce = "GTC"


@dataclass(slots=True)
class FillEvent:
    execution_id:    str
    client_order_id: str
    status:          ExecutionStatus
    side:            OrderSide
    requested_price: float | None
    filled_price:    float | None
    qty:             float
    fees:            float
    slippage_bps:    float
    executed_at:     datetime
