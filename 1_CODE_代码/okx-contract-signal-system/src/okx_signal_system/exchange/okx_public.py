"""Read-only OKX public market-data adapter."""
from __future__ import annotations

import logging
import socket
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from okx_signal_system.timeframe import timeframe_spec

BASE_URL = "https://www.okx.com"
DEFAULT_LOCAL_PROXY = "http://127.0.0.1:1088"

log = logging.getLogger(__name__)


def _tcp_port_open(host: str, port: int, timeout: float = 0.25) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _okx_rest_proxy_url() -> str | None:
    import os

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


def _request_public(path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
    try:
        import requests
    except ModuleNotFoundError as exc:
        raise RuntimeError("requests is required for OKX network calls") from exc

    params = params or {}
    url = BASE_URL + path

    def send(proxies: dict[str, str] | None = None):
        kwargs: dict[str, Any] = {"timeout": 15, "headers": {"Content-Type": "application/json"}}
        if proxies:
            kwargs["proxies"] = proxies
        return requests.get(url, params=params or None, **kwargs)

    try:
        resp = send()
    except requests.RequestException as exc:
        proxy_url = _okx_rest_proxy_url()
        if not proxy_url:
            query = f"?{urlencode(params)}" if params else ""
            raise ConnectionError(f"OKX public REST network error for {path}{query}: {exc}") from exc
        try:
            log.info("Retrying OKX public REST via proxy %s after direct request failed", proxy_url)
            resp = send(_proxy_dict(proxy_url))
        except requests.RequestException as proxy_exc:
            raise ConnectionError(f"OKX public REST network error: {exc}; proxy retry failed: {proxy_exc}") from proxy_exc

    if resp.status_code != 200:
        raise ConnectionError(f"OKX public REST error: {resp.status_code} {resp.text}")

    result = resp.json()
    if result.get("code") != "0":
        raise ConnectionError(f"OKX public REST error: {result.get('msg')} (code={result.get('code')})")
    return result


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def get_ticker(inst_id: str) -> dict[str, Any]:
    result = _request_public("/api/v5/market/ticker", {"instId": inst_id})
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
    result = _request_public("/api/v5/market/history-candles", params)
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


def test_connection() -> dict[str, Any]:
    try:
        get_ticker("BTC-USDT-SWAP")
    except Exception as exc:
        return {"connected": False, "simulated": True, "reason": str(exc)}
    return {"connected": True, "simulated": True, "reason": "public_market_data_only"}
