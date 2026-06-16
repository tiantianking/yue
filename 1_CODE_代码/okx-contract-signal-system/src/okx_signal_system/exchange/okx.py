"""OKX exchange adapter.

The module keeps network side effects explicit:
- public market data works without credentials;
- private account and trade endpoints require environment variables;
- simulated trading is enabled by default unless OKX_IS_SIMULATED=false.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import socket
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

from okx_signal_system.timeframe import timeframe_spec

BASE_URL = "https://www.okx.com"
PRIVATE_PATH_PREFIXES = ("/api/v5/account/", "/api/v5/trade/")
DEFAULT_LOCAL_PROXY = "http://127.0.0.1:1088"

log = logging.getLogger(__name__)

OKX_API_KEY = os.environ.get("OKX_API_KEY", "")
OKX_SECRET_KEY = os.environ.get("OKX_SECRET_KEY", "")
OKX_PASSPHRASE = os.environ.get("OKX_PASSPHRASE", "")
OKX_IS_SIMULATED = os.environ.get("OKX_IS_SIMULATED", "true").lower() != "false"
LIVE_ORDER_ENV = "OKX_LIVE_ORDER_ENABLED"


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _is_private_path(path: str) -> bool:
    return path.startswith(PRIVATE_PATH_PREFIXES)


def _credentials_ready() -> bool:
    return bool(OKX_API_KEY and OKX_SECRET_KEY and OKX_PASSPHRASE)


def _env_bool(name: str) -> bool | None:
    value = os.environ.get(name)
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    return None


def _live_order_enabled() -> bool:
    env_value = _env_bool(LIVE_ORDER_ENV)
    if env_value is not None:
        return env_value
    try:
        from okx_signal_system.config import load_config

        cfg = load_config("base.yaml")
        return bool(cfg.get("execution", {}).get("live_order_enabled", False))
    except Exception:
        return False


def _assert_live_order_allowed() -> None:
    if not _live_order_enabled():
        raise RuntimeError(
            "live order execution is disabled; enable execution.live_order_enabled "
            f"or set {LIVE_ORDER_ENV}=true only after manual approval"
        )


def _sign(message: str) -> str:
    if not OKX_SECRET_KEY:
        raise RuntimeError("OKX_SECRET_KEY is not configured")
    mac = hmac.new(OKX_SECRET_KEY.encode("utf-8"), message.encode("utf-8"), hashlib.sha256)
    return base64.b64encode(mac.digest()).decode("utf-8")


def _headers(method: str, request_path: str) -> dict[str, str]:
    if not _is_private_path(request_path):
        return {"Content-Type": "application/json"}

    if not _credentials_ready():
        raise RuntimeError("OKX API credentials are not configured")

    timestamp = _utc_now()
    signature = _sign(timestamp + method + request_path)
    headers = {
        "OK-ACCESS-KEY": OKX_API_KEY,
        "OK-ACCESS-SIGN": signature,
        "OK-ACCESS-TIMESTAMP": timestamp,
        "OK-ACCESS-PASSPHRASE": OKX_PASSPHRASE,
        "Content-Type": "application/json",
    }
    if OKX_IS_SIMULATED:
        headers["x-simulated-trading"] = "1"
    return headers


def _tcp_port_open(host: str, port: int, timeout: float = 0.25) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _okx_rest_proxy_url() -> str | None:
    configured = os.environ.get("OKX_REST_PROXY", "").strip()
    if configured.lower() in {"0", "false", "off", "none"}:
        return None
    if configured:
        return configured
    if _tcp_port_open("127.0.0.1", 1088):
        return DEFAULT_LOCAL_PROXY
    return None


def _proxy_dict(proxy_url: str | None) -> dict[str, str] | None:
    if not proxy_url:
        return None
    return {"http": proxy_url, "https": proxy_url}


def _request(method: str, path: str, params: dict | None = None) -> dict[str, Any]:
    try:
        import requests
    except ModuleNotFoundError as exc:
        raise RuntimeError("requests is required for OKX network calls") from exc

    method = method.upper()
    params = params or {}
    request_path = f"{path}?{urlencode(params)}" if method == "GET" and params else path
    url = BASE_URL + path
    headers = _headers(method, request_path)

    def send(proxies: dict[str, str] | None = None):
        request_kwargs = {"headers": headers, "timeout": 15}
        if proxies:
            request_kwargs["proxies"] = proxies
        if method == "GET":
            return requests.get(url, params=params or None, **request_kwargs)
        if method == "POST":
            return requests.post(url, json=params, **request_kwargs)
        if method == "DELETE":
            return requests.delete(url, json=params or None, **request_kwargs)
        raise ValueError(f"unsupported HTTP method: {method}")

    try:
        resp = send()
    except requests.RequestException as exc:
        proxy_url = _okx_rest_proxy_url()
        if not proxy_url:
            raise ConnectionError(f"OKX network error: {exc}") from exc
        try:
            log.info("Retrying OKX REST via proxy %s after direct request failed", proxy_url)
            resp = send(_proxy_dict(proxy_url))
        except requests.RequestException as proxy_exc:
            raise ConnectionError(f"OKX network error: {exc}; proxy retry failed: {proxy_exc}") from proxy_exc

    if resp.status_code != 200:
        raise ConnectionError(f"OKX API error: {resp.status_code} {resp.text}")

    result = resp.json()
    if result.get("code") != "0":
        raise ConnectionError(f"OKX API error: {result.get('msg')} (code={result.get('code')})")
    return result


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def get_account_balance(ccy: str = "BTC") -> dict[str, Any]:
    result = _request("GET", "/api/v5/account/balance", {"ccy": ccy})
    data = result.get("data", [{}])[0]
    for detail in data.get("details", []):
        if detail.get("ccy") == ccy:
            return {
                "ccy": ccy,
                "avail_eq": _to_float(detail.get("availEq")),
                "eq": _to_float(detail.get("eq")),
                "eq_usd": _to_float(detail.get("eqUsd")),
            }
    return {"ccy": ccy, "avail_eq": 0.0, "eq": 0.0, "eq_usd": 0.0}


def get_account_positions(inst_id: str | None = None) -> list[dict[str, Any]]:
    params: dict[str, str] = {"instType": "SWAP"}
    if inst_id and inst_id.upper() not in {"SWAP", "FUTURES"}:
        params["instId"] = inst_id

    result = _request("GET", "/api/v5/account/positions", params)
    positions = []
    for pos in result.get("data", []):
        size = _to_float(pos.get("pos"))
        if size == 0:
            continue
        positions.append(
            {
                "inst_id": pos.get("instId", ""),
                "side": (pos.get("posSide") or "net").lower(),
                "size": abs(size),
                "entry_price": _to_float(pos.get("avgPx")),
                "unrealized_pnl": _to_float(pos.get("upl")),
                "margin": _to_float(pos.get("margin")),
                "leverage": _to_float(pos.get("lever")),
                "raw": pos,
            }
        )
    return positions


@dataclass
class OrderParams:
    inst_id: str
    side: str
    size: float | str
    price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    order_type: str = "market"
    td_mode: str = "isolated"
    reduce_only: bool = False


def place_order(params: OrderParams) -> dict[str, Any]:
    if params.side not in {"buy", "sell"}:
        raise ValueError("side must be buy or sell")
    if float(params.size) <= 0:
        raise ValueError("size must be positive")
    if params.stop_loss is not None or params.take_profit is not None:
        raise ValueError("TP/SL protection must be handled explicitly; place_order does not attach TP/SL orders")
    _assert_live_order_allowed()

    body: dict[str, Any] = {
        "instId": params.inst_id,
        "tdMode": params.td_mode,
        "side": params.side,
        "ordType": "limit" if params.price is not None else params.order_type,
        "sz": str(params.size),
        "clOrdId": f"sig_{int(time.time() * 1000)}"[:32],
    }
    if params.price is not None:
        body["px"] = str(params.price)
    if params.reduce_only:
        body["reduceOnly"] = "true"

    result = _request("POST", "/api/v5/trade/order", body)
    return result.get("data", [{}])[0]


def close_position(inst_id: str, size: float | str | None = None) -> dict[str, Any]:
    positions = get_account_positions(inst_id)
    if not positions:
        return {"code": "1", "msg": "no open position", "data": []}

    position = positions[0]
    close_size = str(size if size is not None else position["size"])
    side = "sell" if position["side"] == "long" else "buy"
    order = OrderParams(
        inst_id=inst_id,
        side=side,
        size=close_size,
        order_type="market",
        reduce_only=True,
    )
    data = place_order(order)
    return {"code": "0", "msg": "", "data": [data]}


def get_open_orders(inst_id: str | None = None) -> list[dict[str, Any]]:
    params: dict[str, str] = {"instType": "SWAP", "state": "live"}
    if inst_id:
        params["instId"] = inst_id
    return _request("GET", "/api/v5/trade/orders-pending", params).get("data", [])


def get_ticker(inst_id: str) -> dict[str, Any]:
    result = _request("GET", "/api/v5/market/ticker", {"instId": inst_id})
    data = result.get("data", [{}])[0]
    return {
        "inst_id": data.get("instId", inst_id),
        "last": _to_float(data.get("last")),
        "bid": _to_float(data.get("bidPx")),
        "ask": _to_float(data.get("askPx")),
        "vol_24h": _to_float(data.get("vol24h")),
        "ts": data.get("ts", ""),
    }


def get_candles(
    inst_id: str,
    bar: str = "1h",
    limit: int = 100,
    *,
    before: str | int | None = None,
    after: str | int | None = None,
) -> list[list[Any]]:
    okx_bar = timeframe_spec(bar).okx_bar
    params = {"instId": inst_id, "bar": okx_bar, "limit": str(limit)}
    if before is not None:
        params["before"] = str(before)
    if after is not None:
        params["after"] = str(after)
    result = _request(
        "GET",
        "/api/v5/market/history-candles",
        params,
    )
    return result.get("data", [])


@dataclass
class OKXInstrument:
    base: str
    quote: str = "USDT"
    contract_type: str = "SWAP"

    @property
    def inst_id(self) -> str:
        return f"{self.base}-{self.quote}-{self.contract_type}"

    @classmethod
    def from_symbol(cls, symbol: str) -> "OKXInstrument":
        normalized = symbol.replace("_", "-").upper()
        parts = normalized.split("-")
        if len(parts) == 3 and parts[1] == "USDT" and parts[2] == "USDT":
            return cls(base=parts[0], quote="USDT")
        if len(parts) == 3:
            base, quote, contract_type = parts
            return cls(base=base, quote=quote, contract_type=contract_type)
        if len(parts) == 2:
            base, quote = parts
            return cls(base=base, quote=quote)
        if normalized.endswith("USDT"):
            return cls(base=normalized[:-4], quote="USDT")
        raise ValueError(f"cannot convert symbol to OKX instrument: {symbol}")


def okx_place_order_preview(
    *,
    inst_id: str,
    side: str,
    size: float,
    price: float | None,
    client_order_id: str,
    margin_mode: str = "isolated",
) -> dict[str, str]:
    if margin_mode != "isolated":
        raise ValueError("only isolated margin is allowed")
    if not inst_id.endswith("-SWAP"):
        raise ValueError("only OKX SWAP instruments are allowed")
    if side not in {"buy", "sell"}:
        raise ValueError("side must be buy or sell")
    if size <= 0:
        raise ValueError("size must be positive")
    body = {
        "instId": inst_id,
        "tdMode": margin_mode,
        "side": side,
        "ordType": "market" if price is None else "limit",
        "sz": str(size),
        "clOrdId": client_order_id[:32],
    }
    if price is not None:
        body["px"] = str(price)
    return body


def test_connection() -> dict[str, Any]:
    if not _credentials_ready():
        return {
            "connected": False,
            "balance": None,
            "simulated": OKX_IS_SIMULATED,
            "reason": "missing_credentials",
        }
    return {
        "connected": True,
        "balance": get_account_balance("BTC"),
        "simulated": OKX_IS_SIMULATED,
    }
