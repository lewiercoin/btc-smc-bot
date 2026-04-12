from __future__ import annotations

import hashlib
import hmac
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

import requests

LOG = logging.getLogger(__name__)


@dataclass(slots=True)
class RestClientConfig:
    base_url: str
    timeout_seconds: int
    max_retries: int = 3
    retry_backoff_seconds: float = 0.75
    api_key: str = ""
    api_secret: str = ""
    recv_window_ms: int = 5000


class RestClientError(RuntimeError):
    pass


class BinanceRequestError(RestClientError):
    def __init__(
        self,
        *,
        path: str,
        method: str,
        status_code: int | None = None,
        code: int | None = None,
        message: str | None = None,
    ) -> None:
        self.path = path
        self.method = method
        self.status_code = status_code
        self.code = code
        self.message = message or "Binance request failed."
        super().__init__(self.__str__())

    def __str__(self) -> str:
        details = [f"method={self.method}", f"path={self.path}"]
        if self.status_code is not None:
            details.append(f"http={self.status_code}")
        if self.code is not None:
            details.append(f"code={self.code}")
        details.append(f"msg={self.message}")
        return "BinanceRequestError(" + ", ".join(details) + ")"


def _ms_to_utc(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def normalize_kline(payload: list[Any], symbol: str, timeframe: str) -> dict[str, Any]:
    if len(payload) < 6:
        raise ValueError("Invalid kline payload.")
    return {
        "symbol": symbol.upper(),
        "timeframe": timeframe,
        "open_time": _ms_to_utc(int(payload[0])),
        "open": float(payload[1]),
        "high": float(payload[2]),
        "low": float(payload[3]),
        "close": float(payload[4]),
        "volume": float(payload[5]),
    }


def normalize_funding(payload: dict[str, Any], symbol: str) -> dict[str, Any]:
    return {
        "symbol": symbol.upper(),
        "funding_time": _ms_to_utc(int(payload["fundingTime"])),
        "funding_rate": float(payload["fundingRate"]),
    }


def normalize_open_interest(payload: dict[str, Any], symbol: str) -> dict[str, Any]:
    timestamp_ms = int(payload.get("time") or payload.get("timestamp") or int(datetime.now(timezone.utc).timestamp() * 1000))
    return {
        "symbol": symbol.upper(),
        "timestamp": _ms_to_utc(timestamp_ms),
        "oi_value": float(payload["openInterest"]),
    }


def normalize_open_interest_hist(payload: dict[str, Any], symbol: str) -> dict[str, Any]:
    return {
        "symbol": symbol.upper(),
        "timestamp": _ms_to_utc(int(payload["timestamp"])),
        "oi_value": float(payload["sumOpenInterest"]),
    }


def normalize_book_ticker(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "symbol": str(payload["symbol"]).upper(),
        "bid": float(payload["bidPrice"]),
        "ask": float(payload["askPrice"]),
    }


def normalize_agg_trade(payload: dict[str, Any], symbol: str) -> dict[str, Any]:
    return {
        "symbol": symbol.upper(),
        "event_time": _ms_to_utc(int(payload["T"])),
        "price": float(payload["p"]),
        "qty": float(payload["q"]),
        "is_buyer_maker": bool(payload["m"]),
    }


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def _to_float(value: Any, default: float = 0.0) -> float:
    if value in (None, ""):
        return default
    return float(value)


def normalize_position_risk(payload: dict[str, Any]) -> dict[str, Any]:
    raw_amt = _to_float(payload.get("positionAmt"), 0.0)
    position_side = str(payload.get("positionSide", "BOTH")).upper()
    direction: str | None = None
    if raw_amt > 0:
        direction = "LONG"
    elif raw_amt < 0:
        direction = "SHORT"
    elif position_side in {"LONG", "SHORT"}:
        direction = position_side

    return {
        "symbol": str(payload.get("symbol", "")).upper(),
        "direction": direction,
        "position_side": position_side,
        "size": abs(raw_amt),
        "leverage": int(payload.get("leverage", 0)),
        "isolated": _to_bool(payload.get("isolated", False)),
    }


def normalize_open_order(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "symbol": str(payload.get("symbol", "")).upper(),
        "order_id": str(payload.get("orderId", "")),
        "side": str(payload.get("side", "")).upper(),
        "position_side": str(payload.get("positionSide", "BOTH")).upper(),
        "status": str(payload.get("status", "")).upper(),
        "type": str(payload.get("type", "")).upper(),
        "price": _to_float(payload.get("price"), 0.0),
        "orig_qty": _to_float(payload.get("origQty"), 0.0),
    }


class BinanceFuturesRestClient:
    def __init__(self, config: RestClientConfig, session: requests.Session | None = None) -> None:
        self.config = config
        self.session = session or requests.Session()

    def _request(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self._request_with_retry(
            method="GET",
            path=path,
            params=params,
            signed=False,
        )

    def signed_request(self, path: str, params: dict[str, Any] | None = None, method: str = "GET") -> Any:
        if not self.config.api_key or not self.config.api_secret:
            raise RestClientError("Signed Binance request requires API key and API secret.")
        return self._request_with_retry(
            method=method,
            path=path,
            params=params,
            signed=True,
        )

    def _request_with_retry(
        self,
        *,
        method: str,
        path: str,
        params: dict[str, Any] | None,
        signed: bool,
    ) -> Any:
        method_upper = method.upper()
        base_params = dict(params or {})
        headers: dict[str, str] = {}

        if signed:
            base_params["recvWindow"] = int(self.config.recv_window_ms)
            headers["X-MBX-APIKEY"] = self.config.api_key

        url = f"{self.config.base_url.rstrip('/')}{path}"
        error: Exception | None = None
        for attempt in range(self.config.max_retries + 1):
            request_params = dict(base_params)
            if signed:
                request_params["timestamp"] = int(datetime.now(timezone.utc).timestamp() * 1000)
                query = urlencode(request_params, doseq=True)
                request_params["signature"] = hmac.new(
                    self.config.api_secret.encode("utf-8"),
                    query.encode("utf-8"),
                    hashlib.sha256,
                ).hexdigest()

            try:
                response = self.session.request(
                    method_upper,
                    url,
                    params=request_params,
                    headers=headers or None,
                    timeout=self.config.timeout_seconds,
                )
                if response.status_code >= 400:
                    request_error = self._parse_binance_error(path, method_upper, response)
                    if response.status_code >= 500 and attempt < self.config.max_retries:
                        sleep_seconds = self.config.retry_backoff_seconds * (2**attempt)
                        LOG.warning(
                            "REST retry %s/%s for %s %s after http=%s code=%s msg=%s",
                            attempt + 1,
                            self.config.max_retries,
                            method_upper,
                            path,
                            request_error.status_code,
                            request_error.code,
                            request_error.message,
                        )
                        time.sleep(sleep_seconds)
                        continue
                    raise request_error

                if not response.text:
                    return {}
                return response.json()
            except (requests.RequestException, ValueError) as exc:
                error = exc
                if attempt >= self.config.max_retries:
                    break
                sleep_seconds = self.config.retry_backoff_seconds * (2**attempt)
                LOG.warning(
                    "REST retry %s/%s for %s %s after error: %s",
                    attempt + 1,
                    self.config.max_retries,
                    method_upper,
                    path,
                    exc,
                )
                time.sleep(sleep_seconds)
            except BinanceRequestError as exc:
                error = exc
                break

        if isinstance(error, BinanceRequestError):
            raise error
        request_kind = "signed request" if signed else "request"
        raise RestClientError(f"Failed {request_kind} {method_upper} {path} after retries") from error

    @staticmethod
    def _parse_binance_error(path: str, method: str, response: requests.Response) -> BinanceRequestError:
        code: int | None = None
        message: str | None = None
        try:
            payload = response.json()
            if isinstance(payload, dict):
                raw_code = payload.get("code")
                if raw_code is not None:
                    code = int(raw_code)
                raw_message = payload.get("msg")
                if raw_message:
                    message = str(raw_message)
        except ValueError:
            message = response.text.strip() or None

        return BinanceRequestError(
            path=path,
            method=method,
            status_code=response.status_code,
            code=code,
            message=message or "Request rejected by Binance.",
        )

    def fetch_klines(
        self,
        symbol: str,
        interval: str,
        limit: int = 500,
        start_time_ms: int | None = None,
        end_time_ms: int | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"symbol": symbol.upper(), "interval": interval, "limit": int(limit)}
        if start_time_ms is not None:
            params["startTime"] = int(start_time_ms)
        if end_time_ms is not None:
            params["endTime"] = int(end_time_ms)
        payload = self._request("/fapi/v1/klines", params)
        return [normalize_kline(item, symbol=symbol, timeframe=interval) for item in payload]

    def fetch_funding_history(
        self,
        symbol: str,
        limit: int = 200,
        start_time_ms: int | None = None,
        end_time_ms: int | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"symbol": symbol.upper(), "limit": int(limit)}
        if start_time_ms is not None:
            params["startTime"] = int(start_time_ms)
        if end_time_ms is not None:
            params["endTime"] = int(end_time_ms)
        payload = self._request("/fapi/v1/fundingRate", params)
        return [normalize_funding(item, symbol=symbol) for item in payload]

    def fetch_open_interest(self, symbol: str) -> dict[str, Any]:
        payload = self._request("/fapi/v1/openInterest", {"symbol": symbol.upper()})
        return normalize_open_interest(payload, symbol=symbol)

    def fetch_open_interest_history(
        self,
        symbol: str,
        period: str = "5m",
        limit: int = 200,
        start_time_ms: int | None = None,
        end_time_ms: int | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"symbol": symbol.upper(), "period": period, "limit": int(limit)}
        if start_time_ms is not None:
            params["startTime"] = int(start_time_ms)
        if end_time_ms is not None:
            params["endTime"] = int(end_time_ms)
        payload = self._request("/futures/data/openInterestHist", params)
        if not isinstance(payload, list):
            raise RestClientError("Unexpected openInterestHist response payload.")
        return [normalize_open_interest_hist(item, symbol=symbol) for item in payload]

    def fetch_book_ticker(self, symbol: str) -> dict[str, Any]:
        payload = self._request("/fapi/v1/ticker/bookTicker", {"symbol": symbol.upper()})
        return normalize_book_ticker(payload)

    def fetch_exchange_info(self) -> dict[str, Any]:
        return self._request("/fapi/v1/exchangeInfo")

    def ping(self) -> bool:
        payload = self._request("/fapi/v1/ping")
        if isinstance(payload, dict):
            return True
        return payload == {}

    def fetch_agg_trades(
        self,
        symbol: str,
        limit: int = 1000,
        start_time_ms: int | None = None,
        end_time_ms: int | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"symbol": symbol.upper(), "limit": int(limit)}
        if start_time_ms is not None:
            params["startTime"] = int(start_time_ms)
        if end_time_ms is not None:
            params["endTime"] = int(end_time_ms)

        payload = self._request("/fapi/v1/aggTrades", params)
        return [normalize_agg_trade(item, symbol=symbol) for item in payload]

    def fetch_position_risk(self, symbol: str) -> list[dict[str, Any]]:
        payload = self.signed_request("/fapi/v2/positionRisk", {"symbol": symbol.upper()})
        if not isinstance(payload, list):
            raise RestClientError("Unexpected positionRisk response payload.")
        return [normalize_position_risk(item) for item in payload]

    def fetch_open_orders(self, symbol: str) -> list[dict[str, Any]]:
        payload = self.signed_request("/fapi/v1/openOrders", {"symbol": symbol.upper()})
        if not isinstance(payload, list):
            raise RestClientError("Unexpected openOrders response payload.")
        return [normalize_open_order(item) for item in payload]

    def fetch_active_positions(self, symbol: str) -> list[dict[str, Any]]:
        active: list[dict[str, Any]] = []
        for position in self.fetch_position_risk(symbol):
            if position["symbol"] != symbol.upper():
                continue
            if float(position["size"]) <= 0:
                continue
            active.append(position)
        return active
