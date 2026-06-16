"""Compatibility exports for the read-only OKX public adapter."""
from __future__ import annotations

from okx_signal_system.exchange.okx_public import (
    OKXInstrument,
    _okx_rest_proxy_url,
    _proxy_dict,
    get_candles,
    get_ticker,
    test_connection,
)

__all__ = [
    "OKXInstrument",
    "_okx_rest_proxy_url",
    "_proxy_dict",
    "get_candles",
    "get_ticker",
    "test_connection",
]
