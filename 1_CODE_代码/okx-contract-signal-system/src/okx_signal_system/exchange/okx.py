"""
OKX 交易所适配器
"""
from __future__ import annotations

import hashlib
import hmac
import base64
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import requests

# ============================================================
# API 凭证配置
# ============================================================
OKX_API_KEY = "8fcab04d-14e3-4b88-89bb-b9beac7b9ad7"
OKX_SECRET_KEY = "7ED970F8EC9892659726CD39275F1318"
OKX_PASSPHRASE = "@Wang19861103"
OKX_IS_SIMULATED = True  # 模拟盘


def _utc_now() -> str:
    """生成OKX要求的ISO 8601时间戳"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _sign(message: str) -> str:
    """HMAC-SHA256签名"""
    mac = hmac.new(
        OKX_SECRET_KEY.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    )
    return base64.b64encode(mac.digest()).decode("utf-8")


def _headers(method: str, path: str) -> dict[str, str]:
    """生成带签名的请求头"""
    timestamp = _utc_now()
    message = timestamp + method + path
    signature = _sign(message)

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


BASE_URL = "https://www.okx.com"


def _request(method: str, path: str, params: dict | None = None) -> dict[str, Any]:
    """通用请求方法"""
    url = BASE_URL + path

    # 构建带查询参数的完整path（用于签名）
    if params:
        query_string = "&".join(f"{k}={v}" for k, v in params.items())
        full_path = f"{path}?{query_string}"
    else:
        full_path = path

    headers = _headers(method, full_path)
    try:
        if method == "GET":
            resp = requests.get(url, headers=headers, params=params, timeout=15)
        elif method == "POST":
            resp = requests.post(url, headers=headers, json=params or {}, timeout=15)
        elif method == "DELETE":
            resp = requests.delete(url, headers=headers, timeout=15)
        else:
            raise ValueError(f"Unsupported method: {method}")

        if resp.status_code != 200:
            raise ConnectionError(f"OKX API错误: {resp.status_code} {resp.text}")

        result = resp.json()
        if result.get("code") != "0":
            raise ConnectionError(f"OKX API错误: {result.get('msg')} (code={result.get('code')})")

        return result
    except requests.RequestException as e:
        raise ConnectionError(f"OKX网络错误: {e}")


# ============================================================
# 账户查询
# ============================================================
def get_account_balance(ccy: str = "BTC") -> dict[str, Any]:
    """获取账户余额"""
    result = _request("GET", "/api/v5/account/balance", {"ccy": ccy})
    data = result["data"][0]
    details = data.get("details", [])
    for d in details:
        if d["ccy"] == ccy:
            return {
                "ccy": ccy,
                "avail_eq": float(d["availEq"]),
                "eq": float(d["eq"]),
                "eq_usd": float(d["eqUsd"]),
            }
    return {"ccy": ccy, "avail_eq": 0.0, "eq": 0.0, "eq_usd": 0.0}


def get_account_positions(inst_id: str | None = None) -> list[dict[str, Any]]:
    """获取持仓"""
    inst_family = inst_id.split("-")[0] if inst_id else None
    params = {"instType": "SWAP"}
    if inst_id:
        params["instId"] = inst_id
    result = _request("GET", "/api/v5/account/positions", params)
    positions = []
    for pos in result.get("data", []):
        if inst_family and not pos["instId"].startswith(inst_family):
            continue
        positions.append({
            "inst_id": pos["instId"],
            "side": pos["posSide"].lower(),
            "size": float(pos["pos"]),
            "entry_price": float(pos["avgPx"]) if pos.get("avgPx") else 0.0,
            "unrealized_pnl": float(pos["upl"]) if pos.get("upl") else 0.0,
            "margin": float(pos["margin"]) if pos.get("margin") else 0.0,
            "leverage": float(pos["lever"]) if pos.get("lever") else 0.0,
        })
    return positions


# ============================================================
# 订单操作
# ============================================================
@dataclass
class OrderParams:
    """下单参数"""
    inst_id: str
    side: str  # buy/sell
    size: float
    price: float | None = None  # None=市价单
    stop_loss: float | None = None
    take_profit: float | None = None


def place_order(params: OrderParams) -> dict[str, Any]:
    """市价开仓"""
    body = {
        "instId": params.inst_id,
        "tdMode": "isolated",
        "side": params.side,
        "ordType": "market",
        "sz": str(params.size),
        "clOrdId": f"sig_{int(time.time()*1000)}",
    }
    if params.price is not None:
        body["ordType"] = "limit"
        body["px"] = str(params.price)

    result = _request("POST", "/api/v5/trade/order", body)

    order_id = result["data"][0]["ordId"]

    # 设置止损止盈
    if params.stop_loss or params.take_profit:
        _set_sl_tp(order_id, params)

    return result["data"][0]


def _set_sl_tp(ord_id: str, params: OrderParams) -> None:
    """设置止损止盈"""
    inst_id = params.inst_id
    if params.stop_loss:
        sl_side = "sell" if params.side == "buy" else "buy"
        _request("POST", "/api/v5/trade/order", {
            "instId": inst_id,
            "tdMode": "isolated",
            "side": sl_side,
            "ordType": "market",
            "sz": "0",
            "slTriggerPx": str(params.stop_loss),
            "slOrdType": "market",
        })
    if params.take_profit:
        tp_side = "sell" if params.side == "buy" else "buy"
        _request("POST", "/api/v5/trade/order", {
            "instId": inst_id,
            "tdMode": "isolated",
            "side": tp_side,
            "ordType": "market",
            "sz": "0",
            "tpTriggerPx": str(params.take_profit),
            "tpOrdType": "market",
        })


def close_position(inst_id: str, size: float) -> dict[str, Any]:
    """市价平仓"""
    positions = get_account_positions(inst_id)
    if not positions:
        return {"msg": "无持仓"}

    pos = positions[0]
    side = "sell" if pos["side"] == "long" else "buy"

    body = {
        "instId": inst_id,
        "tdMode": "isolated",
        "side": side,
        "ordType": "market",
        "sz": str(size),
        "clOrdId": f"close_{int(time.time()*1000)}",
    }
    result = _request("POST", "/api/v5/trade/order", body)
    return result["data"][0]


def get_open_orders(inst_id: str | None = None) -> list[dict[str, Any]]:
    """获取未成交订单"""
    params = {"instType": "SWAP", "state": "live"}
    if inst_id:
        params["instId"] = inst_id
    result = _request("GET", "/api/v5/trade/orders-pending", params)
    return result.get("data", [])


# ============================================================
# 实时行情
# ============================================================
def get_ticker(inst_id: str) -> dict[str, Any]:
    """获取实时行情"""
    result = _request("GET", "/api/v5/market/ticker", {"instId": inst_id})
    d = result["data"][0]
    return {
        "inst_id": d["instId"],
        "last": float(d["last"]),
        "bid": float(d["bidPx"]),
        "ask": float(d["askPx"]),
        "vol_24h": float(d["vol24h"]),
        "ts": d["ts"],
    }


def get_candles(inst_id: str, bar: str = "1h", limit: int = 100) -> list[list[Any]]:
    """获取K线数据"""
    result = _request("GET", "/api/v5/market/history-candles", {
        "instId": inst_id,
        "bar": bar,
        "limit": str(limit),
    })
    return result.get("data", [])


# ============================================================
# 工具函数
# ============================================================
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
    """下单预览（不实际下单）"""
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
        "tdMode": "isolated",
        "side": side,
        "ordType": "market" if price is None else "limit",
        "sz": str(size),
        "clOrdId": client_order_id[:32],
    }
    if price is not None:
        body["px"] = str(price)
    return body


def test_connection() -> dict[str, Any]:
    """测试API连接"""
    balance = get_account_balance("BTC")
    return {
        "connected": True,
        "balance": balance,
        "simulated": OKX_IS_SIMULATED,
    }