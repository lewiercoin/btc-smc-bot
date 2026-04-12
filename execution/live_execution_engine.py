"""Live execution engine for btc-smc-bot.

Places real Binance Futures orders (LIMIT entry, STOP_MARKET SL, TAKE_PROFIT_MARKET TP).
Copied from btc-bot/execution/live_execution_engine.py and adapted:
- Removed dependency on ExecutableSignal (uses flat params instead)
- Imports from local execution_types and connectors
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from uuid import uuid4

from connectors.rest_client import BinanceFuturesRestClient, BinanceRequestError, RestClientError
from execution.execution_engine import ExecutionEngine, PositionPersister
from execution.execution_types import ExecutionStatus, FillEvent, OrderRequest
from monitoring.audit_logger import AuditLogger


class LiveExecutionError(RuntimeError):
    pass


class OrderManagerError(RuntimeError):
    pass


class LiveExecutionEngine(ExecutionEngine):
    def __init__(
        self,
        *,
        position_persister:       PositionPersister,
        rest_client:              BinanceFuturesRestClient,
        audit_logger:             AuditLogger,
        symbol:                   str = "BTCUSDT",
        entry_order_type:         str = "LIMIT",
        entry_timeout_seconds:    int = 90,
        poll_interval_seconds:    float = 1.0,
    ) -> None:
        self.position_persister    = position_persister
        self.rest_client           = rest_client
        self.audit_logger          = audit_logger
        self.symbol                = symbol.upper()
        self.entry_order_type      = entry_order_type.upper()
        self.entry_timeout_seconds = max(int(entry_timeout_seconds), 1)
        self.poll_interval_seconds = max(float(poll_interval_seconds), 0.0)

    def execute_signal(
        self,
        signal_id:     str,
        direction:     str,  # "LONG" or "SHORT" (uppercase)
        entry_price:   float,
        stop_loss:     float,
        take_profit_1: float,
        take_profit_2: float,
        size:          float,
        leverage:      int,
    ) -> None:
        """Place entry order, wait for fill, then place SL and TP orders."""
        try:
            self._set_leverage(leverage)
            entry_side    = "BUY" if direction == "LONG" else "SELL"
            entry_request = self._build_entry_order(
                signal_id=signal_id,
                entry_price=entry_price,
                size=size,
                side=entry_side,
            )
            entry_coid   = self._submit_order(entry_request)
            fill_result  = self._wait_for_entry_fill(entry_coid, entry_request)
        except (RestClientError, BinanceRequestError) as exc:
            self.audit_logger.log_error(
                "live_execution", "Entry failed.",
                payload={"signal_id": signal_id, "error": str(exc)},
            )
            raise LiveExecutionError(f"entry_failed:{exc}") from exc

        if fill_result.filled_qty <= 0:
            raise LiveExecutionError("entry_failed:zero_fill")

        position_id = f"live-{uuid4().hex}"
        opened_at   = fill_result.executed_at

        self.position_persister.insert_position(
            position_id=position_id,
            signal_id=signal_id,
            symbol=self.symbol,
            direction=direction,
            status="OPEN" if fill_result.fully_filled else "PARTIAL",
            entry_price=fill_result.avg_fill_price,
            size=fill_result.filled_qty,
            leverage=int(leverage),
            stop_loss=stop_loss,
            take_profit_1=take_profit_1,
            take_profit_2=take_profit_2,
            opened_at=opened_at,
            updated_at=opened_at,
        )
        self.position_persister.commit()

        # Place SL and TP1 protective orders
        try:
            exit_side  = "SELL" if direction == "LONG" else "BUY"
            sl_request = OrderRequest(
                client_order_id=f"sl-{signal_id[:16]}-{uuid4().hex[:8]}",
                symbol=self.symbol,
                side=exit_side,
                order_type="STOP_MARKET",
                qty=fill_result.filled_qty,
                stop_price=stop_loss,
            )
            tp_request = OrderRequest(
                client_order_id=f"tp-{signal_id[:16]}-{uuid4().hex[:8]}",
                symbol=self.symbol,
                side=exit_side,
                order_type="TAKE_PROFIT_MARKET",
                qty=fill_result.filled_qty,
                stop_price=take_profit_1,
            )
            self._submit_order(sl_request)
            self._submit_order(tp_request)
            self.position_persister.commit()
            self.audit_logger.log_info(
                "live_execution", "Execution complete.",
                payload={"signal_id": signal_id, "position_id": position_id},
            )
        except (RestClientError, BinanceRequestError) as exc:
            self.audit_logger.log_error(
                "live_execution", "Protective orders failed.",
                payload={"signal_id": signal_id, "position_id": position_id, "error": str(exc)},
            )
            raise LiveExecutionError(f"protective_order_failed:{exc}") from exc

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _set_leverage(self, leverage: int) -> None:
        self.rest_client.signed_request(
            "/fapi/v1/leverage",
            params={"symbol": self.symbol, "leverage": int(leverage)},
            method="POST",
        )

    def _build_entry_order(
        self, *, signal_id: str, entry_price: float, size: float, side: str
    ) -> OrderRequest:
        coid = f"entry-{signal_id[:16]}-{uuid4().hex[:8]}"
        if self.entry_order_type == "MARKET":
            return OrderRequest(
                client_order_id=coid, symbol=self.symbol,
                side=side, order_type="MARKET", qty=float(size),
            )
        return OrderRequest(
            client_order_id=coid, symbol=self.symbol,
            side=side, order_type="LIMIT", qty=float(size),
            price=float(entry_price), time_in_force="GTC",
        )

    def _submit_order(self, request: OrderRequest) -> str:
        params: dict = {
            "symbol":           request.symbol,
            "side":             request.side,
            "type":             request.order_type,
            "quantity":         f"{request.qty:.4f}",
            "newClientOrderId": request.client_order_id,
        }
        if request.order_type == "LIMIT" and request.price:
            params["price"]         = f"{request.price:.2f}"
            params["timeInForce"]   = request.time_in_force
        if request.stop_price and request.order_type in {"STOP_MARKET", "TAKE_PROFIT_MARKET"}:
            params["stopPrice"] = f"{request.stop_price:.2f}"

        resp = self.rest_client.signed_request("/fapi/v1/order", params=params, method="POST")
        return str(resp.get("clientOrderId") or request.client_order_id)

    def _wait_for_entry_fill(self, client_order_id: str, request: OrderRequest) -> "_FillResult":
        deadline = time.monotonic() + self.entry_timeout_seconds
        while True:
            payload = self.rest_client.signed_request(
                "/fapi/v1/order",
                params={"symbol": self.symbol, "origClientOrderId": client_order_id},
            )
            raw_status    = str(payload.get("status", "NEW")).upper()
            executed_qty  = float(payload.get("executedQty", 0))
            avg_price     = float(payload.get("avgPrice", 0) or 0)
            event_ts      = self._payload_time(payload)

            if raw_status == "FILLED":
                return _FillResult(
                    filled_qty=executed_qty,
                    avg_fill_price=avg_price or (request.price or 0.0),
                    fully_filled=True,
                    executed_at=event_ts,
                )
            if raw_status == "REJECTED":
                raise LiveExecutionError(f"entry_rejected:{client_order_id}")
            if time.monotonic() >= deadline:
                if executed_qty > 0:
                    try:
                        self.rest_client.signed_request(
                            "/fapi/v1/order",
                            params={"symbol": self.symbol, "origClientOrderId": client_order_id},
                            method="DELETE",
                        )
                    except Exception:
                        pass
                    return _FillResult(
                        filled_qty=executed_qty,
                        avg_fill_price=avg_price or (request.price or 0.0),
                        fully_filled=False,
                        executed_at=event_ts,
                    )
                raise LiveExecutionError(f"entry_timeout:{client_order_id}")

            if self.poll_interval_seconds > 0:
                time.sleep(self.poll_interval_seconds)

    @staticmethod
    def _payload_time(payload: dict) -> datetime:
        raw = payload.get("updateTime") or payload.get("transactTime")
        if raw is None:
            return datetime.now(timezone.utc)
        try:
            return datetime.fromtimestamp(int(raw) / 1000, tz=timezone.utc)
        except (TypeError, ValueError):
            return datetime.now(timezone.utc)


class _FillResult:
    def __init__(
        self, *, filled_qty: float, avg_fill_price: float,
        fully_filled: bool, executed_at: datetime,
    ) -> None:
        self.filled_qty      = filled_qty
        self.avg_fill_price  = avg_fill_price
        self.fully_filled    = fully_filled
        self.executed_at     = executed_at
