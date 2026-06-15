"""
OKX 合约信号系统 - 实时交易所API模块
实时获取市场数据，自动下单 + 数据持久化
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import time
import threading
import contextlib
import io
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Callable
import gzip

import pandas as pd

from okx_signal_system.exchange.candles import okx_candles_to_frame
from okx_signal_system.exchange.okx import (
    get_ticker,
    get_candles,
    get_account_balance,
    get_account_positions,
    place_order,
    close_position,
    OrderParams,
    test_connection,
)
from okx_signal_system.paths import find_lightweight_history
from okx_signal_system.strategy.trend_breakout import (
    build_signal, TradeSignal, StrategyParams, 
    generate_signals
)
from okx_signal_system.risk.model import (
    validate_signal, RiskConfig, Ledger, RiskDecision, 
    apply_halt_policy, COST_BUFFER_RATE
)
from okx_signal_system.features.indicators import build_feature_frame
from okx_signal_system.signal_runtime import (
    DEFAULT_MAX_SIGNAL_LAG_MINUTES,
    seconds_until_next_signal_scan,
    signal_is_stale,
)
from okx_signal_system.signal_quality import SignalCandidate, SignalLifecycleStore, assign_tiers, lifecycle_payload
from okx_signal_system.timeframe import default_trend_timeframe, ratio_bars, timeframe_spec

log = logging.getLogger(__name__)
DEFAULT_LOCAL_PROXY = "http://127.0.0.1:1088"


def _tcp_port_open(host: str, port: int, timeout: float = 0.25) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _okx_ws_proxy_url() -> str | None:
    configured = os.environ.get("OKX_WS_PROXY", "").strip()
    if configured.lower() in {"0", "false", "off", "none"}:
        return None
    if configured:
        return configured
    if _tcp_port_open("127.0.0.1", 1088):
        return DEFAULT_LOCAL_PROXY
    return None


def _websocket_proxy_options(proxy_url: str | None) -> dict[str, Any]:
    if not proxy_url:
        return {}
    from urllib.parse import urlparse

    parsed = urlparse(proxy_url)
    if not parsed.hostname:
        return {}
    options: dict[str, Any] = {
        "http_proxy_host": parsed.hostname,
        "http_proxy_port": parsed.port or (443 if parsed.scheme == "https" else 80),
        "proxy_type": (parsed.scheme or "http").lower(),
        "http_proxy_timeout": 8,
    }
    if parsed.username:
        options["http_proxy_auth"] = (parsed.username, parsed.password or "")
    return options

# 缓存配置
CACHE_TTL_SECONDS = 5  # 缓存5秒
RECONNECT_DELAY = 3  # 重连延迟3秒
AI_PERSIST_INTERVAL = 60  # 每60秒写入磁盘


def _live_signal_history_limit(params: StrategyParams, *, signal_timeframe: str, trend_timeframe: str) -> int:
    trend_ratio = ratio_bars(trend_timeframe, signal_timeframe)
    return max(
        600,
        params.slow_ema + params.breakout_window + 120,
        params.slow_ema * trend_ratio + 160,
    )


def _read_parquet_with_retry(path: Path, attempts: int = 3) -> pd.DataFrame:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            return pd.read_parquet(path)
        except Exception as exc:
            last_error = exc
            if attempt < attempts - 1:
                time.sleep(0.2 * (attempt + 1))
    assert last_error is not None
    raise last_error


def _write_parquet_atomic(frame: pd.DataFrame, path: Path) -> None:
    tmp_path = path.with_name(f"{path.stem}.{os.getpid()}.tmp{path.suffix}")
    frame.to_parquet(tmp_path, index=False)
    tmp_path.replace(path)


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, pd.Timestamp)):
        return value.isoformat()
    if hasattr(value, "item"):
        return value.item()
    return str(value)


def _write_json_atomic(payload: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.stem}.{os.getpid()}.tmp{path.suffix}")
    tmp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )
    tmp_path.replace(path)


@dataclass
class MarketData:
    """市场数据"""
    inst_id: str
    last_price: float
    bid_price: float
    ask_price: float
    volume_24h: float
    timestamp: datetime
    # K线数据
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    volume: float = 0.0


@dataclass
class OrderRequest:
    """订单请求"""
    inst_id: str
    side: Literal["open_long", "open_short", "close_long", "close_short"]
    size: float
    price: float | None = None  # None = 市价单
    reduce_only: bool = False
    stop_loss: float | None = None
    take_profit: float | None = None


@dataclass
class OrderResponse:
    """订单响应"""
    order_id: str
    inst_id: str
    side: str
    size: float
    price: float
    filled_size: float
    avg_price: float
    status: Literal["live", "filled", "cancelled", "rejected"]
    timestamp: datetime


@dataclass
class Position:
    """持仓信息"""
    inst_id: str
    side: Literal["long", "short", "net"]
    size: float
    entry_price: float
    unrealized_pnl: float
    margin: float
    leverage: float
    liquidation_price: float | None


@dataclass
class AccountBalance:
    """账户余额"""
    total_equity: float
    available: float
    margin_used: float
    total_pnl: float
    margin_ratio: float


class RealtimeDataStore:
    """
    实时数据存储器
    轻量化存储 + 断网恢复
    """

    def __init__(
        self,
        data_dir: Path | None = None,
        *,
        timeframe: str = "15m",
        dataset: str | None = None,
    ):
        self.timeframe = timeframe_spec(timeframe)
        if data_dir is None:
            data_dir = find_lightweight_history(dataset or f"okx_{self.timeframe.file_suffix}_extended")
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # 内存缓存: {inst_id: DataFrame}
        self._cache: dict[str, pd.DataFrame] = {}
        # 最后写入时间
        self._last_write: dict[str, datetime] = {}

    def _get_file_path(self, inst_id: str) -> Path:
        """获取文件路径"""
        normalized = inst_id.replace("-", "_").replace("_SWAP", "").upper()
        # BTC-USDT-SWAP -> BTC_USDT_USDT_<timeframe>.parquet
        if normalized.count("USDT") == 1:
            normalized = f"{normalized}_USDT"
        return self.data_dir / f"{normalized}_{self.timeframe.file_suffix}.parquet"

    def load(self, inst_id: str) -> pd.DataFrame:
        """加载数据"""
        if inst_id in self._cache:
            return self._cache[inst_id]

        path = self._get_file_path(inst_id)
        if path.exists():
            df = _read_parquet_with_retry(path)
            df["ts"] = pd.to_datetime(df["ts"], utc=True)
            self._cache[inst_id] = df
            return df

        # 返回空DataFrame
        return pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume", "quote_volume"])

    def append_candle(self, inst_id: str, candle: dict) -> bool:
        """
        添加K线数据
        candle格式: {"ts": ..., "open": ..., "high": ..., "low": ..., "close": ..., "volume": ...}
        """
        df = self.load(inst_id)

        quote_volume = candle.get("quote_volume")
        if quote_volume is None:
            quote_volume = candle.get("volCcyQuote")
        new_row = pd.DataFrame([{
            "ts": pd.to_datetime(candle["ts"], utc=True),
            "open": float(candle["open"]),
            "high": float(candle["high"]),
            "low": float(candle["low"]),
            "close": float(candle["close"]),
            "volume": float(candle["volume"]),
            "quote_volume": float(quote_volume) if quote_volume not in (None, "") else float("nan"),
            "symbol": inst_id,
            "timeframe": self.timeframe.key,
            "is_closed": bool(candle.get("is_closed", True)),
        }])

        # Merge then de-duplicate instead of assigning by row; old parquet
        # columns may have stricter dtypes than live float payloads.
        df = pd.concat([df, new_row], ignore_index=True)
        df["ts"] = pd.to_datetime(df["ts"], utc=True)
        df = df.drop_duplicates(subset=["ts"], keep="last").sort_values("ts").reset_index(drop=True)

        # 保持最新1000根K线在内存
        if len(df) > 1000:
            df = df.tail(1000).reset_index(drop=True)

        self._cache[inst_id] = df
        return True

    def persist_if_needed(self, inst_id: str) -> bool:
        """必要时写入磁盘"""
        if inst_id not in self._cache:
            return False

        now = datetime.now(timezone.utc)
        last_write = self._last_write.get(inst_id)

        # 每60秒或新K线闭合时写入
        should_write = (
            last_write is None or
            (now - last_write).total_seconds() >= AI_PERSIST_INTERVAL
        )

        if should_write:
            return self.save(inst_id)
        return False

    def save(self, inst_id: str) -> bool:
        """保存到磁盘"""
        if inst_id not in self._cache:
            return False

        try:
            df = self._cache[inst_id]
            path = self._get_file_path(inst_id)

            # 读取现有数据合并
            if path.exists():
                existing = _read_parquet_with_retry(path)
                existing["ts"] = pd.to_datetime(existing["ts"], utc=True)
                df_combined = pd.concat([existing, df], ignore_index=True)
                df_combined = df_combined.drop_duplicates(subset=["ts"], keep="last")
                df_combined = df_combined.sort_values("ts").reset_index(drop=True)
                _write_parquet_atomic(df_combined, path)
                self._cache[inst_id] = df_combined
            else:
                _write_parquet_atomic(df, path)

            self._last_write[inst_id] = datetime.now(timezone.utc)
            log.debug(f"Saved {len(df)} bars for {inst_id}")
            return True
        except Exception as e:
            log.error(f"Save error for {inst_id}: {e}")
            return False

    def save_all(self) -> dict[str, bool]:
        """保存所有缓存数据"""
        results = {}
        for inst_id in list(self._cache.keys()):
            results[inst_id] = self.save(inst_id)
        return results


class OKXWebSocketClient:
    """
    OKX WebSocket客户端
    处理实时K线推送 + 自动重连
    """

    def __init__(
        self,
        on_candle: Callable[[str, dict], None] | None = None,
        on_ticker: Callable[[str, dict], None] | None = None,
        *,
        timeframe: str = "15m",
    ):
        self.timeframe = timeframe_spec(timeframe)
        self._candle_channel = self.timeframe.ws_channel
        self._ws: Any | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._subscriptions: set[str] = set()
        self._reconnect_count = 0
        self._max_reconnect = 10
        self._degraded = False
        self._last_error: str | None = None
        self._opened = threading.Event()
        self._connected = False
        self._last_message_at: float | None = None
        self._last_open_at: float | None = None
        self._last_close: dict[str, Any] | None = None

        # 回调函数
        self.on_candle = on_candle
        self.on_ticker = on_ticker

        # 数据缓存
        self._candle_cache: dict[str, dict] = {}

    def _get_wss_url(self) -> str:
        """获取公共行情 WebSocket URL。"""
        configured = os.environ.get("OKX_PUBLIC_WS_URL", "").strip()
        if configured:
            return configured
        return "wss://ws.okx.com:8443/ws/v5/business"

    def _is_expected_ws_disconnect(self, error: Any) -> bool:
        text = str(error or "").lower()
        return "10054" in text or "goodbye" in text or "远程主机强迫关闭" in text

    def connect(self, subscriptions: list[str]) -> bool:
        """连接WebSocket"""
        if self._running:
            self.disconnect()

        self._subscriptions = set(subscriptions)
        self._running = True
        self._reconnect_count = 0
        self._degraded = False
        self._last_error = None
        self._connected = False
        self._opened.clear()

        try:
            self._start_websocket()
            if not self._opened.wait(timeout=12):
                self._degraded = True
                self._last_error = "websocket_open_timeout"
                log.error("WebSocket did not open within 12s")
                return False
            return self._connected
        except Exception as e:
            self._degraded = True
            self._last_error = str(e)
            log.error(f"WebSocket connect error; REST fallback will stay available: {e}")
            self._thread = threading.Thread(target=self._handle_disconnect, daemon=True)
            self._thread.start()
            return False

    def _start_websocket(self):
        """启动WebSocket连接"""
        try:
            import websocket
        except ModuleNotFoundError as exc:
            raise RuntimeError("websocket-client is required for OKX WebSocket") from exc

        url = self._get_wss_url()

        def on_message(ws, message):
            try:
                data = json.loads(message)
                self._last_message_at = time.time()
                self._handle_message(data)
            except Exception as e:
                log.error(f"WebSocket message error: {e}")

        def on_error(ws, error):
            self._connected = False
            self._degraded = True
            self._last_error = str(error)
            if self._is_expected_ws_disconnect(error):
                log.warning("WebSocket disconnected by remote peer; REST fallback remains active")
            else:
                log.error(f"WebSocket error: {error}")

        def on_close(ws, close_status_code, close_msg):
            self._connected = False
            self._last_close = {"code": close_status_code, "message": close_msg}
            close_text = f"{close_status_code} {close_msg}"
            if self._is_expected_ws_disconnect(close_text):
                log.warning("WebSocket closed by remote peer; REST fallback remains active")
            else:
                log.warning(f"WebSocket closed: {close_status_code} {close_msg}")
            self._handle_disconnect()

        def on_open(ws):
            log.info("WebSocket connected")
            self._connected = True
            self._degraded = False
            self._last_error = None
            self._last_open_at = time.time()
            self._opened.set()
            self._reconnect_count = 0
            self._subscribe_channels()

        self._ws = websocket.WebSocketApp(
            url,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
            on_open=on_open,
        )

        def run_ws():
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self._ws.run_forever(
                    ping_interval=20,
                    ping_timeout=10,
                    **_websocket_proxy_options(_okx_ws_proxy_url()),
                )

        self._thread = threading.Thread(target=run_ws, daemon=True)
        self._thread.start()

    def _subscribe_channels(self):
        """订阅频道"""
        if not self._ws or not self._running:
            return

        # 订阅K线数据
        for inst_id in self._subscriptions:
            args = [{
                "channel": self._candle_channel,
                "instId": inst_id,
            }]
            msg = {
                "op": "subscribe",
                "args": args
            }
            self._ws.send(json.dumps(msg))
            log.info(f"Subscribed: {inst_id} {self._candle_channel}")

    def _handle_message(self, data: dict):
        """处理接收到的消息"""
        if "event" in data:
            if data.get("event") == "error":
                self._degraded = True
                self._last_error = f"{data.get('code')}: {data.get('msg')}"
                log.error("WebSocket subscription error: %s", self._last_error)
            return  # 订阅确认等事件

        if "data" not in data:
            return

        arg = data.get("arg", {})
        channel = arg.get("channel", "")
        inst_id = arg.get("instId", "")
        items = data.get("data", [])

        if channel == self._candle_channel:
            for item in items:
                # K线格式: [ts, open, high, low, close, vol, volCcy, volCcyQuote, confirm]
                confirm = str(item[8]) if len(item) > 8 else "1"
                frame = okx_candles_to_frame([item])
                if frame.empty:
                    continue
                row = frame.iloc[-1]
                candle = {
                    "ts": row["ts"],
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row["volume"]),
                    "quote_volume": float(row["quote_volume"]) if pd.notna(row.get("quote_volume")) else None,
                    "is_closed": confirm == "1",
                }
                self._candle_cache[inst_id] = candle

                # 回调
                if self.on_candle:
                    self.on_candle(inst_id, candle)

        elif channel == "tickers":
            for item in items:
                ticker = {
                    "inst_id": item.get("instId", inst_id),
                    "last": float(item.get("last", 0)),
                    "bid": float(item.get("bidPx", 0)),
                    "ask": float(item.get("askPx", 0)),
                    "vol_24h": float(item.get("vol24h", 0)),
                    "ts": item.get("ts", ""),
                }
                if self.on_ticker:
                    self.on_ticker(inst_id, ticker)

    def _handle_disconnect(self):
        """Keep the client alive; REST polling covers data while WS reconnects."""
        self._degraded = True
        while self._running:
            self._reconnect_count += 1
            delay = min(RECONNECT_DELAY * max(1, self._reconnect_count), 60)
            log.warning(
                "WebSocket disconnected; reconnecting in %ss (attempt %s). REST fallback remains active.",
                delay,
                self._reconnect_count,
            )
            time.sleep(delay)
            if not self._running:
                return

            try:
                self._start_websocket()
                return
            except Exception as e:
                self._last_error = str(e)
                log.exception("WebSocket reconnect attempt %s failed", self._reconnect_count)

    def disconnect(self):
        """断开连接"""
        self._running = False
        self._degraded = False
        self._connected = False
        if self._ws:
            self._ws.close()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

    def status(self) -> dict[str, Any]:
        return {
            "running": self._running,
            "connected": self._connected,
            "degraded": self._degraded,
            "reconnect_count": self._reconnect_count,
            "last_error": self._last_error,
            "last_open_at": self._last_open_at,
            "last_message_at": self._last_message_at,
            "last_close": self._last_close,
            "url": self._get_wss_url(),
            "proxy": _okx_ws_proxy_url(),
            "subscriptions": sorted(self._subscriptions),
        }

    def get_latest_candle(self, inst_id: str) -> dict | None:
        """获取最新K线"""
        return self._candle_cache.get(inst_id)


class OKXRealtimeAPI:
    """
    OKX实时API接口
    功能：
    1. WebSocket实时K线推送
    2. 获取实时行情
    3. 查询持仓
    4. 查询账户
    5. 下单/撤单
    6. 数据持久化
    """

    def __init__(self, config: dict | None = None):
        if config is None:
            try:
                from okx_signal_system.config import load_config
                config = load_config("base.yaml")
            except Exception:
                config = {}
        self.config = config or {}
        data_cfg = self.config.get("data", {}) if isinstance(self.config, dict) else {}
        self.timeframe = timeframe_spec(data_cfg.get("timeframe", "15m"))
        self.trend_timeframe = timeframe_spec(
            data_cfg.get("trend_timeframe") or default_trend_timeframe(self.timeframe.key)
        )
        self.dataset = data_cfg.get("historical_dataset") or f"okx_{self.timeframe.file_suffix}_extended"
        self._connected = False
        self._ws_client: OKXWebSocketClient | None = None
        self._data_store = RealtimeDataStore(timeframe=self.timeframe.key, dataset=self.dataset)

        # 监控的币种列表
        self._watched_symbols: list[str] = []
        # 缓存
        self._ticker_cache: dict[str, tuple[dict, float]] = {}
        # 最后同步时间
        self._last_sync = datetime.now(timezone.utc)
        self._sync_gap_seconds = 0

    def _on_candle(self, inst_id: str, candle: dict):
        """K线回调"""
        # 存储到本地
        self._data_store.append_candle(inst_id, candle)
        # 转换为分钟K线
        self._update_ticker_from_candle(inst_id, candle)

    def _on_ticker(self, inst_id: str, ticker: dict):
        """Ticker回调"""
        self._ticker_cache[inst_id] = (ticker, time.time())

    def _update_ticker_from_candle(self, inst_id: str, candle: dict):
        """从K线更新ticker数据"""
        if inst_id in self._ticker_cache:
            ticker, _ = self._ticker_cache[inst_id]
            ticker = dict(ticker)
        else:
            ticker = {"inst_id": inst_id, "bid": 0, "ask": 0, "vol_24h": 0, "ts": ""}

        ticker["last"] = candle["close"]
        self._ticker_cache[inst_id] = (ticker, time.time())

    async def connect(self, symbols: list[str] | None = None) -> bool:
        """连接交易所WebSocket（幂等：已连接时直接返回True）"""
        if self._connected and self._ws_client and self._ws_client._running:
            log.info("Already connected to OKX WebSocket, skipping reconnect")
            return True

        if symbols is None:
            # 默认监控配置中的币种
            from okx_signal_system.config import load_config
            try:
                cfg = load_config("base.yaml")
                symbols = cfg.get("data", {}).get("symbols", ["BTC-USDT-SWAP"])
            except:
                symbols = ["BTC-USDT-SWAP"]

        self._watched_symbols = symbols
        log.info(
            "Connecting to OKX WebSocket for %s symbols (%s signal / %s trend)...",
            len(symbols),
            self.timeframe.key,
            self.trend_timeframe.key,
        )

        # 先测试API连接
        try:
            result = test_connection()
            log.info(f"OKX API connection: {result}")
        except Exception as e:
            log.error(f"OKX API connection failed: {e}")

        # 启动WebSocket
        self._ws_client = OKXWebSocketClient(
            on_candle=self._on_candle,
            on_ticker=self._on_ticker,
            timeframe=self.timeframe.key,
        )

        success = self._ws_client.connect(symbols)
        self._connected = bool(success)

        if success:
            log.info(f"OKX WebSocket connected, watching {len(symbols)} symbols")
        else:
            log.error("OKX WebSocket unavailable; live monitor not fully healthy")

        return success

    async def disconnect(self):
        """断开连接"""
        if self._ws_client:
            self._ws_client.disconnect()
        # 保存所有缓存数据
        self._data_store.save_all()
        self._connected = False
        log.info("OKX API disconnected")

    def is_connected(self) -> bool:
        """检查连接状态"""
        return bool(self._connected and self._ws_client and self._ws_client.status().get("connected"))

    async def get_market_data(self, inst_id: str) -> MarketData | None:
        """
        获取实时市场数据
        优先使用缓存，网络请求作为后备
        """
        # 检查WebSocket缓存
        if inst_id in self._ticker_cache:
            ticker, cached_time = self._ticker_cache[inst_id]
            if time.time() - cached_time < CACHE_TTL_SECONDS:
                return MarketData(
                    inst_id=ticker.get("inst_id", inst_id),
                    last_price=ticker.get("last", 0),
                    bid_price=ticker.get("bid", 0),
                    ask_price=ticker.get("ask", 0),
                    volume_24h=ticker.get("vol_24h", 0),
                    timestamp=datetime.now(timezone.utc),
                )

        # 尝试从本地数据获取
        local_data = self._data_store.load(inst_id)
        if len(local_data) > 0:
            last_row = local_data.iloc[-1]
            return MarketData(
                inst_id=inst_id,
                last_price=float(last_row["close"]),
                bid_price=float(last_row["close"]) * 0.9999,
                ask_price=float(last_row["close"]) * 1.0001,
                volume_24h=float(last_row["volume"]) if "volume" in local_data.columns else 0,
                timestamp=last_row["ts"].to_pydatetime() if hasattr(last_row["ts"], "to_pydatetime") else last_row["ts"],
                open=float(last_row["open"]),
                high=float(last_row["high"]),
                low=float(last_row["low"]),
                close=float(last_row["close"]),
                volume=float(last_row["volume"]),
            )

        # 从OKX API获取
        try:
            data = get_ticker(inst_id)
            self._ticker_cache[inst_id] = (data, time.time())
            return MarketData(
                inst_id=data["inst_id"],
                last_price=data["last"],
                bid_price=data["bid"],
                ask_price=data["ask"],
                volume_24h=data["vol_24h"],
                timestamp=datetime.now(timezone.utc),
            )
        except Exception as e:
            log.error(f"Failed to get market data for {inst_id}: {e}")
            return None

    async def get_candles(self, inst_id: str, bar: str | None = None, limit: int = 100) -> pd.DataFrame:
        """获取K线数据（优先本地，必要时从API补数据）"""
        bar = timeframe_spec(bar or self.timeframe.key).key
        # 先尝试本地数据
        local = self._data_store.load(inst_id)

        needs_refresh = True
        if len(local) > 0:
            try:
                latest = pd.to_datetime(local["ts"].iloc[-1], utc=True)
                age_hours = (pd.Timestamp.now(tz="UTC") - latest).total_seconds() / 3600
                refresh_after_hours = max(self.timeframe.hours * 1.5, 5 / 60)
                needs_refresh = age_hours > refresh_after_hours
            except Exception:
                needs_refresh = True

        if len(local) >= limit and not needs_refresh:
            return local.tail(limit).reset_index(drop=True)

        # 本地数据不足，从API获取
        try:
            from okx_signal_system.exchange.okx import get_candles as okx_get_candles
            raw_bars = okx_get_candles(inst_id, bar=bar, limit=limit)
            raw_bars = [row for row in raw_bars if len(row) < 9 or str(row[8]) == "1"]

            if raw_bars:
                df = okx_candles_to_frame(raw_bars)
                df["symbol"] = inst_id
                df["timeframe"] = bar
                df["is_closed"] = True

                # 合并到本地
                if len(local) > 0:
                    combined = pd.concat([local, df], ignore_index=True)
                    combined = combined.drop_duplicates(subset=["ts"], keep="last")
                    combined = combined.sort_values("ts").reset_index(drop=True)
                    self._data_store._cache[inst_id] = combined
                else:
                    self._data_store._cache[inst_id] = df
                self._data_store.save(inst_id)

                return self._data_store.load(inst_id).tail(limit).reset_index(drop=True)
        except Exception as e:
            log.error(f"Failed to get candles for {inst_id}: {e}")

        return local

    async def get_positions(self, inst_id: str | None = None) -> list[Position]:
        """获取持仓"""
        try:
            positions = get_account_positions(inst_id)
            return [
                Position(
                    inst_id=p["inst_id"],
                    side=p["side"],
                    size=p["size"],
                    entry_price=p["entry_price"],
                    unrealized_pnl=p["unrealized_pnl"],
                    margin=p["margin"],
                    leverage=p["leverage"],
                    liquidation_price=None,
                )
                for p in positions
            ]
        except Exception as e:
            log.debug(f"Failed to get positions (normal for simulated mode): {e}")
            return []

    async def get_account_balance(self) -> AccountBalance:
        """获取账户余额"""
        try:
            balance = get_account_balance("USDT")
            return AccountBalance(
                total_equity=balance.get("eq_usd", 0),
                available=balance.get("avail_eq", 0),
                margin_used=0,
                total_pnl=0,
                margin_ratio=0,
            )
        except Exception as e:
            log.error(f"Failed to get account balance: {e}")
            return AccountBalance(0, 0, 0, 0, 0)

    async def place_order(self, order: OrderRequest) -> OrderResponse | None:
        """下单"""
        if not self._connected:
            log.error("Not connected to OKX")
            return None

        try:
            # 转换side
            if order.side == "open_long":
                side = "buy"
            elif order.side == "open_short":
                side = "sell"
            elif order.side == "close_long":
                side = "sell"
            else:  # close_short
                side = "buy"

            params = OrderParams(
                inst_id=order.inst_id,
                side=side,
                size=order.size,
                price=order.price,
                stop_loss=order.stop_loss,
                take_profit=order.take_profit,
            )

            result = place_order(params)

            return OrderResponse(
                order_id=result.get("ordId", ""),
                inst_id=order.inst_id,
                side=side,
                size=order.size,
                price=order.price or 0,
                filled_size=float(result.get("fillSz", order.size)),
                avg_price=float(result.get("avgPx", order.price or 0)),
                status="filled",
                timestamp=datetime.now(timezone.utc),
            )
        except Exception as e:
            log.error(f"Order failed: {e}")
            return None

    async def cancel_order(self, order_id: str) -> bool:
        """撤单"""
        log.info(f"Cancel order: {order_id}")
        return True

    def persist_data(self) -> dict[str, bool]:
        """持久化所有数据到磁盘"""
        return self._data_store.save_all()

    def sync_from_api(self, inst_id: str) -> int:
        """从API同步数据到本地"""
        try:
            raw_bars = get_candles(inst_id, bar=self.timeframe.key, limit=300)
            raw_bars = [row for row in raw_bars if len(row) < 9 or str(row[8]) == "1"]
            if not raw_bars:
                return 0

            df = okx_candles_to_frame(raw_bars)

            count = 0
            for _, row in df.iterrows():
                candle = {
                    "ts": row["ts"],
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row["volume"]) if pd.notna(row["volume"]) else 0,
                    "quote_volume": float(row["quote_volume"]) if "quote_volume" in row and pd.notna(row["quote_volume"]) else None,
                    "is_closed": True,
                }
                self._data_store.append_candle(inst_id, candle)
                count += 1

            self._data_store.save(inst_id)
            return count
        except Exception as e:
            log.error(f"Sync error for {inst_id}: {e}")
            return 0


class LiveSignalMonitor:
    """
    实时信号监控器
    持续监控市场，产出信号并推送
    """

    def __init__(self, api: OKXRealtimeAPI, signal_callback=None, risk_config: dict | None = None):
        self.api = api
        self.signal_callback = signal_callback
        self.risk_config = risk_config or {
            "stop_loss_pct": 0.02,
            "take_profit_pct": 0.04,
        }
        self._running = False
        self._monitor_task: asyncio.Task | None = None

        # --- 风控模型 ---
        initial_equity = float(os.environ.get("INITIAL_EQUITY", 10000))
        self._risk_cfg = RiskConfig(initial_equity=initial_equity)
        self._ledger = Ledger(
            inst_id="portfolio",
            init_capital=initial_equity,
            equity=initial_equity,
        )

        # --- 市场环境自适应（延迟导入避免循环依赖） ---
        from okx_signal_system.ml.regime_adaptive import AdaptiveParamsManager
        from okx_signal_system.ml.shadow_trading import ShadowTradingLedger
        from okx_signal_system.training.startup_quality import load_selected_strategy_params
        self._regime_mgr = AdaptiveParamsManager()
        self._strategy_params = load_selected_strategy_params()
        from okx_signal_system.strategy.vote_gate import min_vote_approval_rate
        learning_cfg = self.api.config.get("learning", {}) if isinstance(self.api.config, dict) else {}
        self._min_vote_approval_rate = min_vote_approval_rate(self.api.config if isinstance(self.api.config, dict) else {})
        self._shadow_score_min_closed = int(learning_cfg.get("shadow_score_min_closed_signals", 6))
        self._shadow_ledger = ShadowTradingLedger()
        self._lifecycle_store = SignalLifecycleStore()
        self._quality_gate_allows_push = False
        self._last_candidate_health_report_ts = 0.0
        self._last_ready_signal: dict[str, Any] | None = None
        self._sent_startup_health_report = False
        from okx_signal_system.notify.signal_dedupe import BTierSummaryNotificationStore, SignalNotificationStore

        self._signal_notification_store = SignalNotificationStore()
        self._b_tier_summary_store = BTierSummaryNotificationStore()
        try:
            from okx_signal_system.config import project_paths
            self._scan_status_path = project_paths().output_dir / "latest_scan_status.json"
        except Exception:
            self._scan_status_path = Path("outputs") / "latest_scan_status.json"

        # --- 持仓追踪 (max_hold_bars) ---
        self._position_entries: dict[str, tuple[pd.Timestamp, StrategyParams]] = {}

    def _signal_notification_key(self, signal: TradeSignal) -> str:
        from okx_signal_system.notify.signal_dedupe import signal_notification_key

        return signal_notification_key(
            signal,
            signal_timeframe=self.api.timeframe.key,
            trend_timeframe=self.api.trend_timeframe.key,
            params=self._strategy_params,
        )

    def _mark_signal_notified(self, key: str, signal: TradeSignal, *, score: float | None = None) -> None:
        self._signal_notification_store.mark(
            key,
            {
                "symbol": signal.inst_id,
                "side": signal.side,
                "kline_time": pd.Timestamp(signal.ts).isoformat(),
                "score": float(score) if score is not None else None,
                "signal_timeframe": self.api.timeframe.key,
                "trend_timeframe": self.api.trend_timeframe.key,
                "strategy_version": __import__("okx_signal_system").__version__,
            },
        )

    def _b_tier_summary_key(self, candidates: list[SignalCandidate]) -> str | None:
        if not candidates:
            return None
        from okx_signal_system.notify.signal_dedupe import b_tier_summary_key

        return b_tier_summary_key(
            candidates[0].candle_time,
            signal_timeframe=self.api.timeframe.key,
            trend_timeframe=self.api.trend_timeframe.key,
        )

    def _mark_b_tier_summary_notified(self, key: str, candidates: list[SignalCandidate]) -> None:
        candle_time = candidates[0].candle_time if candidates else None
        self._b_tier_summary_store.mark(
            key,
            {
                "kline_time": pd.Timestamp(candle_time).isoformat() if candle_time is not None else "",
                "candidate_count": len(candidates),
                "signal_timeframe": self.api.timeframe.key,
                "trend_timeframe": self.api.trend_timeframe.key,
            },
        )

    async def start(self):
        """启动监控"""
        if not await self.api.connect():
            log.error("Failed to connect to OKX")
            return False

        self._running = True
        log.info("Live signal monitor started")

        try:
            from okx_signal_system.training.startup_quality import run_startup_quality_gate
            report = run_startup_quality_gate(
                symbols=self.api._watched_symbols or None,
                dataset=self.api.dataset,
                signal_timeframe=self.api.timeframe.key,
                trend_timeframe=self.api.trend_timeframe.key,
                max_symbols=None,
            )
            self._strategy_params = report.strategy_params
            self._quality_gate_allows_push = bool(getattr(report, "push_allowed", report.status == "passed"))
            if not self._quality_gate_allows_push:
                log.warning(
                    "Startup quality gate blocked Feishu push: %s",
                    getattr(report, "push_blocking_reasons", report.reasons),
                )
            elif report.reasons:
                log.warning("Startup quality gate warnings do not block Feishu push: %s", report.reasons)
        except Exception as exc:
            self._quality_gate_allows_push = False
            log.warning("Startup quality gate failed; Feishu push paused: %s", exc)

        # 启动后台持久化任务
        self._monitor_task = asyncio.create_task(self._monitor_loop())
        return True

    @staticmethod
    def _breakout_gap_pct(row: pd.Series | None) -> float | None:
        if row is None:
            return None
        try:
            close = float(row.get("close", 0.0))
            if close <= 0:
                return None
            bias = str(row.get("trend_bias", row.get("bias_4h", "flat")))
            if bias == "long":
                level = float(row.get("breakout_high"))
                return max(0.0, (level - close) / close)
            if bias == "short":
                level = float(row.get("breakout_low"))
                return max(0.0, (close - level) / close)
        except (TypeError, ValueError):
            return None
        return None

    def _candidate_health_item(
        self,
        *,
        inst_id: str,
        reason: str,
        row: pd.Series | None = None,
        signal: TradeSignal | None = None,
        regime: str | None = None,
        final_score: float | None = None,
        risk_reason: str | None = None,
        shadow_adjustment: float | None = None,
        would_push: bool = False,
    ) -> dict[str, Any]:
        raw_score = signal.signal_score if signal and signal.signal_score is not None else None
        side = signal.side if signal and signal.accepted else None
        kline_time = None
        close = None
        if row is not None:
            try:
                kline_time = pd.Timestamp(row.get("ts")).isoformat()
            except Exception:
                kline_time = str(row.get("ts", ""))
            try:
                close = float(row.get("close"))
            except (TypeError, ValueError):
                close = None
        return {
            "symbol": inst_id,
            "reason": reason,
            "risk_reason": risk_reason,
            "would_push": would_push,
            "side": side,
            "kline_time": kline_time,
            "close": close,
            "bias": str(row.get("trend_bias", row.get("bias_4h", ""))) if row is not None else None,
            "regime": regime,
            "raw_score": float(raw_score) if raw_score is not None else None,
            "final_score": float(final_score) if final_score is not None else None,
            "shadow_adjustment": float(shadow_adjustment) if shadow_adjustment is not None else None,
            "breakout_gap_pct": self._breakout_gap_pct(row),
        }

    def _write_latest_scan_status(self, items: list[dict[str, Any]], *, error: str | None = None) -> None:
        try:
            shadow_summary = self._shadow_ledger.summary()
        except Exception:
            shadow_summary = {}
        try:
            lifecycle_summary = self._lifecycle_store.summary()
        except Exception:
            lifecycle_summary = {}
        ws_status = self.api._ws_client.status() if self.api._ws_client else None
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "status": "error" if error else "running",
            "error": error,
            "dataset": self.api.dataset,
            "signal_timeframe": self.api.timeframe.key,
            "trend_timeframe": self.api.trend_timeframe.key,
            "push_allowed": self._quality_gate_allows_push,
            "selected_params": asdict(self._strategy_params),
            "websocket": ws_status,
            "shadow_summary": shadow_summary,
            "lifecycle_summary": lifecycle_summary,
            "symbols_checked": len(items),
            "ready_count": sum(1 for item in items if item.get("would_push")),
            "symbols": items,
            "last_signal": self._last_ready_signal,
        }
        try:
            _write_json_atomic(payload, self._scan_status_path)
        except Exception as exc:
            log.warning("Failed to write latest scan status: %s", exc)

    def _send_candidate_health_report(self, items: list[dict[str, Any]]) -> None:
        try:
            from okx_signal_system.notify.feishu import send_candidate_health_report
            shadow_summary = self._shadow_ledger.summary()

            send_candidate_health_report(
                items=items,
                push_allowed=self._quality_gate_allows_push,
                selected_params={
                    **asdict(self._strategy_params),
                    "signal_timeframe": self.api.timeframe.key,
                    "trend_timeframe": self.api.trend_timeframe.key,
                    "shadow_open": shadow_summary.get("open", 0),
                    "shadow_closed": shadow_summary.get("closed", 0),
                    "shadow_take_profit": shadow_summary.get("take_profit", 0),
                    "shadow_stop_loss": shadow_summary.get("stop_loss", 0),
                    "shadow_avg_quality_score": shadow_summary.get("avg_quality_score", 0.0),
                },
            )
            log.info("Candidate health report sent: %s symbols", len(items))
        except Exception as exc:
            log.warning("Candidate health report failed: %s", exc)

    def _candidate_rank_score(self, *, final_score: float, decision: RiskDecision, shadow_adjustment: float = 0.0) -> float:
        rr = float(decision.risk_reward_ratio or 0.0)
        leverage = float(decision.leverage_used or 0.0)
        return float(final_score) + min(rr, 8.0) * 0.15 + float(shadow_adjustment or 0.0) - max(0.0, leverage - 5.0) * 0.05

    async def _publish_tiered_candidates(
        self,
        candidates: list[SignalCandidate],
        *,
        price_history: dict[str, pd.DataFrame] | None = None,
    ) -> None:
        selection = assign_tiers(candidates, max_tier_a=2, price_history=price_history)
        for candidate in selection.ranked:
            candidate.health_item["tier"] = candidate.tier
            candidate.health_item["rank"] = candidate.rank
            candidate.health_item["rank_score"] = candidate.rank_score
            candidate.health_item["correlation_group"] = candidate.correlation_group
        for candidate in selection.tier_a:
            if self._signal_notification_store.has(candidate.notify_key):
                self._last_ready_signal = candidate.payload
                log.info("Signal already notified; skipping duplicate push: %s %s", candidate.inst_id, candidate.side)
                continue
            signal = candidate.signal
            decision = candidate.decision
            self._last_ready_signal = candidate.payload
            log.info(
                "A-tier signal: %s %s rank=%s score=%.2f",
                candidate.inst_id,
                candidate.side.upper(),
                candidate.rank,
                candidate.rank_score,
            )
            signal_recorded = False
            if self.signal_callback:
                try:
                    callback_result = self.signal_callback(signal, decision)
                    signal_recorded = callback_result is not False
                    if signal_recorded:
                        self._shadow_ledger.record_signal(signal, decision)
                except Exception as cb_err:
                    log.error("Signal callback error: %s", cb_err)
                    signal_recorded = False
            else:
                try:
                    from okx_signal_system.notify.feishu import send_signal_alert

                    signal_recorded = send_signal_alert(
                        inst_id=signal.inst_id,
                        side=signal.side,
                        entry_ref=signal.entry_ref or 0,
                        stop_loss=signal.stop_loss or 0,
                        take_profit=signal.take_profit or 0,
                        qty=decision.qty or 0,
                        leverage=decision.leverage_used,
                        reason=", ".join(signal.reason_codes) if signal.reason_codes else "",
                        signal_score=decision.signal_score,
                        risk_reward_ratio=decision.risk_reward_ratio,
                        stop_reason=decision.stop_reason or "",
                        tp_reason=decision.tp_reason or "",
                        max_loss_pct=getattr(decision, "max_loss_pct", None),
                        margin_loss_pct=getattr(decision, "margin_loss_pct", None),
                        kline_time=pd.Timestamp(signal.ts).isoformat(),
                        signal_timeframe=self.api.timeframe.key,
                        trend_timeframe=self.api.trend_timeframe.key,
                        tier=candidate.tier,
                        rank=candidate.rank,
                        total_candidates=len(selection.ranked),
                        lifecycle_status=(candidate.payload.get("lifecycle") or {}).get("status"),
                        invalidation_price=candidate.invalidation_price,
                    )
                    if signal_recorded:
                        self._shadow_ledger.record_signal(signal, decision)
                        log.info("Feishu A-tier push sent: %s %s", candidate.inst_id, candidate.side)
                except Exception as feishu_err:
                    log.error("Feishu push failed: %s", feishu_err)
                    signal_recorded = False
            if signal_recorded:
                self._mark_signal_notified(candidate.notify_key, signal, score=float(candidate.payload["signal"].get("signal_score") or 0.0))
            else:
                log.warning("Signal alert was not delivered; will retry next scan: %s %s", candidate.inst_id, candidate.side)
        if selection.tier_b:
            log.info("B-tier candidates retained for summary: %s", len(selection.tier_b))
            summary_key = self._b_tier_summary_key(selection.tier_b)
            if summary_key and self._b_tier_summary_store.has(summary_key):
                log.info("B-tier summary already sent for this candle: %s", summary_key)
            else:
                try:
                    from okx_signal_system.notify.feishu import send_b_tier_summary

                    summary_sent = send_b_tier_summary(
                        selection.tier_b,
                        total_candidates=len(selection.ranked),
                        signal_timeframe=self.api.timeframe.key,
                        trend_timeframe=self.api.trend_timeframe.key,
                    )
                except Exception as exc:
                    log.error("B-tier summary push failed: %s", exc)
                    summary_sent = False
                if summary_sent and summary_key:
                    self._mark_b_tier_summary_notified(summary_key, selection.tier_b)
                    log.info("B-tier summary sent: %s candidates", len(selection.tier_b))
                elif summary_key:
                    log.warning("B-tier summary was not delivered; will retry next scan")

    async def _monitor_loop(self):
        """监控循环 — 信号生成 + 风控 + 环境自适应 + 持仓超时"""
        health_interval = float(os.environ.get("SIGNAL_HEALTH_REPORT_INTERVAL_SECONDS", "3600"))

        while self._running:
            cycle_health: list[dict[str, Any]] = []
            ready_candidates: list[SignalCandidate] = []
            candidate_history: dict[str, pd.DataFrame] = {}
            try:
                # 获取持仓（模拟盘可能401，优雅降级）
                positions = []
                try:
                    positions = await self.api.get_positions()
                except Exception as e:
                    log.debug(f"获取持仓失败（模拟盘正常）: {e}")
                pos_inst_ids = {p.inst_id for p in positions}

                # ── 持仓管理: TP/SL + max_hold_bars ──
                for pos in positions:
                    market = await self.api.get_market_data(pos.inst_id)
                    if market:
                        await self._check_exit_conditions(pos, market)
                        await self._check_hold_timeout(pos, market)

                # ── 清理已平仓的追踪记录 ──
                for inst_id in list(self._position_entries):
                    if inst_id not in pos_inst_ids:
                        del self._position_entries[inst_id]

                # ── 信号生成 ──
                symbols = self.api._watched_symbols
                for symbol in symbols:
                    inst_id = symbol if isinstance(symbol, str) else symbol

                    # 已有持仓则跳过
                    if inst_id in pos_inst_ids:
                        cycle_health.append(self._candidate_health_item(inst_id=inst_id, reason="position_open"))
                        continue

                    # 获取K线数据
                    strategy_params = self._strategy_params
                    history_limit = _live_signal_history_limit(
                        strategy_params,
                        signal_timeframe=self.api.timeframe.key,
                        trend_timeframe=self.api.trend_timeframe.key,
                    )
                    df = await self.api.get_candles(inst_id, bar=self.api.timeframe.key, limit=history_limit)
                    from okx_signal_system.data.loader import closed_bars
                    df = closed_bars(df)
                    if len(df) < 50:
                        cycle_health.append(self._candidate_health_item(inst_id=inst_id, reason="history_too_short"))
                        continue
                    candidate_history[inst_id] = df
                    self._shadow_ledger.update_symbol(inst_id, df)
                    self._lifecycle_store.update_symbol(inst_id, df)
                    from okx_signal_system.training.startup_quality import is_latest_bar_fresh
                    from okx_signal_system.data.closed_backfill import latest_closed_candle_start
                    if not is_latest_bar_fresh(df, max_lag_hours=self.api.timeframe.fresh_lag_hours):
                        log.warning(f"{inst_id} latest candle is stale; waiting for live data")
                        cycle_health.append(self._candidate_health_item(inst_id=inst_id, reason="stale_data"))
                        continue
                    expected_closed = pd.Timestamp(latest_closed_candle_start(self.api.timeframe.key, settle_seconds=60))
                    latest_closed = pd.to_datetime(df["ts"].iloc[-1], utc=True)
                    if latest_closed < expected_closed:
                        cycle_health.append(self._candidate_health_item(inst_id=inst_id, reason="missing_latest_closed_bar"))
                        continue
                    if signal_is_stale(
                        latest_closed,
                        timeframe=self.api.timeframe.key,
                        max_lag_minutes=DEFAULT_MAX_SIGNAL_LAG_MINUTES,
                    ):
                        cycle_health.append(self._candidate_health_item(inst_id=inst_id, reason="stale_signal_bar"))
                        continue

                    # 构建特征帧
                    features = build_feature_frame(
                        df,
                        fast_ema=strategy_params.fast_ema,
                        slow_ema=strategy_params.slow_ema,
                        breakout_window=strategy_params.breakout_window,
                        atr_window=strategy_params.atr_window,
                        signal_timeframe=self.api.timeframe.key,
                        trend_timeframe=self.api.trend_timeframe.key,
                    )

                    # 更新市场环境；环境只降分/降杠杆，不覆盖历史训练参数
                    regime, _adaptive_params = self._regime_mgr.update_regime(features)

                    # 生成信号
                    latest_row = features.iloc[-1]
                    signal = build_signal(
                        latest_row,
                        inst_id=inst_id,
                        params=strategy_params,
                        frame=features,
                        idx=len(features) - 1
                    )

                    if not signal.accepted:
                        cycle_health.append(
                            self._candidate_health_item(
                                inst_id=inst_id,
                                reason=signal.reject_reason or "signal_rejected",
                                row=latest_row,
                                signal=signal,
                                regime=regime,
                            )
                        )
                        continue

                    from okx_signal_system.strategy.ensemble import ensemble_vote
                    from okx_signal_system.strategy.vote_gate import vote_gate_passed
                    ensemble_result = ensemble_vote(
                        latest_row,
                        strategy_params,
                        features,
                        len(features) - 1,
                        base_score=signal.signal_score or 5.0,
                        base_signal=signal,
                    )
                    effective_score = ensemble_result.final_score
                    vote_ok = vote_gate_passed(
                        ensemble_result.final_side,
                        signal.side,
                        ensemble_result.approval_rate,
                        self._min_vote_approval_rate,
                    )
                    if ensemble_result.final_side == "flat":
                        effective_score = max(1.0, effective_score - 3.0)
                    elif ensemble_result.final_side != signal.side:
                        effective_score = max(1.0, effective_score - 1.5)
                    penalty = self._regime_mgr.get_score_penalty()
                    if penalty < 0:
                        effective_score = max(1.0, effective_score + penalty)
                    shadow_adjustment = self._shadow_ledger.score_adjustment(
                        inst_id,
                        signal.side,
                        min_closed=self._shadow_score_min_closed,
                    )
                    if shadow_adjustment:
                        effective_score = max(1.0, min(10.0, effective_score + shadow_adjustment))

                    # 风控校验
                    ledger = apply_halt_policy(self._ledger, self._risk_cfg)
                    risk_cfg = replace(
                        self._risk_cfg,
                        max_leverage=max(1.0, min(10.0, self._risk_cfg.max_leverage * self._regime_mgr.get_leverage_factor())),
                    )
                    signal = replace(signal, signal_score=effective_score)
                    decision = validate_signal(signal, ledger, risk_cfg)
                    would_push = bool(
                        decision.accepted
                        and effective_score >= 6.0
                        and self._quality_gate_allows_push
                        and vote_ok
                    )
                    signal_payload = {
                        "signal": asdict(signal),
                        "risk": asdict(decision),
                        "live_order_enabled": False,
                        "mode": "live_scan_manual_confirmation_only",
                        "dataset": self.api.dataset,
                        "signal_timeframe": self.api.timeframe.key,
                        "trend_timeframe": self.api.trend_timeframe.key,
                        "selected_params": asdict(strategy_params),
                    }
                    if ensemble_result.final_side == "flat":
                        health_reason = "vote_flat"
                    elif ensemble_result.final_side != signal.side:
                        health_reason = "vote_side_mismatch"
                    elif ensemble_result.approval_rate < self._min_vote_approval_rate:
                        health_reason = "vote_support_too_low"
                    elif would_push:
                        health_reason = "ready"
                    elif not self._quality_gate_allows_push:
                        health_reason = "quality_gate_blocked"
                    elif not decision.accepted:
                        health_reason = f"risk_{decision.reason or 'rejected'}"
                    elif effective_score < 6.0:
                        health_reason = "score_below_6"
                    else:
                        health_reason = "not_ready"
                    health_item = self._candidate_health_item(
                        inst_id=inst_id,
                        reason=health_reason,
                        row=latest_row,
                        signal=signal,
                        regime=regime,
                        final_score=effective_score,
                        risk_reason=decision.reason,
                        shadow_adjustment=shadow_adjustment,
                        would_push=would_push,
                    )
                    cycle_health.append(health_item)

                    if would_push:
                        notify_key = self._signal_notification_key(signal)
                        lifecycle_record = self._lifecycle_store.record_signal(
                            signal,
                            signal_id=notify_key,
                            invalidation_price=signal.stop_loss,
                            signal_timeframe=self.api.timeframe.key,
                            trend_timeframe=self.api.trend_timeframe.key,
                        )
                        if lifecycle_record is not None:
                            lifecycle = lifecycle_payload(lifecycle_record)
                            signal_payload["signal"]["invalidation_price"] = lifecycle_record.invalidation_price
                            signal_payload["lifecycle"] = lifecycle
                            health_item["invalidation_price"] = lifecycle_record.invalidation_price
                            health_item["lifecycle_status"] = lifecycle_record.status
                            health_item["lifecycle"] = lifecycle
                        ready_candidates.append(
                            SignalCandidate(
                                signal=signal,
                                decision=decision,
                                notify_key=notify_key,
                                payload=signal_payload,
                                health_item=health_item,
                                rank_score=self._candidate_rank_score(
                                    final_score=effective_score,
                                    decision=decision,
                                    shadow_adjustment=shadow_adjustment,
                                ),
                                raw_score=effective_score,
                            )
                        )

                # 持久化
                await self._publish_tiered_candidates(ready_candidates, price_history=candidate_history)
                self.api.persist_data()
                self._write_latest_scan_status(cycle_health)
                now_ts = time.time()
                if (
                    not self._sent_startup_health_report
                    or (
                        health_interval > 0
                        and now_ts - self._last_candidate_health_report_ts >= health_interval
                    )
                ):
                    self._last_candidate_health_report_ts = now_ts
                    self._sent_startup_health_report = True
                    self._send_candidate_health_report(cycle_health)

                await asyncio.sleep(
                    min(seconds_until_next_signal_scan(self.api.timeframe.key, settle_seconds=60), 3600.0)
                )

            except Exception as e:
                log.exception("Monitor error")
                self._write_latest_scan_status(cycle_health, error=str(e))
                await asyncio.sleep(5)

    def stop(self):
        """停止监控"""
        self._running = False
        if self._monitor_task:
            self._monitor_task.cancel()
        log.info("Live signal monitor stopped")

    async def _check_exit_conditions(self, position: Position, market: MarketData):
        """检查是否需要止损止盈"""
        if position.side == "long":
            # 止损
            stop_price = position.entry_price * (1 - self.risk_config.get("stop_loss_pct", 0.02))
            if market.last_price <= stop_price:
                log.warning(f"Stop loss triggered: {position.inst_id}")
                await self.api.place_order(OrderRequest(
                    inst_id=position.inst_id,
                    side="close_long",
                    size=position.size,
                    reduce_only=True,
                ))

            # 止盈
            tp_price = position.entry_price * (1 + self.risk_config.get("take_profit_pct", 0.04))
            if market.last_price >= tp_price:
                log.info(f"Take profit reached: {position.inst_id}")
                await self.api.place_order(OrderRequest(
                    inst_id=position.inst_id,
                    side="close_long",
                    size=position.size,
                    reduce_only=True,
                ))
        else:  # short
            stop_price = position.entry_price * (1 + self.risk_config.get("stop_loss_pct", 0.02))
            if market.last_price >= stop_price:
                log.warning(f"Stop loss triggered: {position.inst_id}")
                await self.api.place_order(OrderRequest(
                    inst_id=position.inst_id,
                    side="close_short",
                    size=position.size,
                    reduce_only=True,
                ))

            tp_price = position.entry_price * (1 - self.risk_config.get("take_profit_pct", 0.04))
            if market.last_price <= tp_price:
                log.info(f"Take profit reached: {position.inst_id}")
                await self.api.place_order(OrderRequest(
                    inst_id=position.inst_id,
                    side="close_short",
                    size=position.size,
                    reduce_only=True,
                ))

    async def _check_hold_timeout(self, position: Position, market: MarketData) -> None:
        """检查持仓超时 (max_hold_bars)：首次出现时记录入场时间，超时则强制平仓"""
        inst_id = position.inst_id

        # 首次出现 → 记录入场时间
        if inst_id not in self._position_entries:
            # 用当前时间近似入场（精确到小时）
            self._position_entries[inst_id] = (
                pd.Timestamp.now(tz="UTC"),
                self._regime_mgr.current_params,
            )
            return

        entry_time, entry_params = self._position_entries[inst_id]
        max_bars = entry_params.max_hold_bars
        now = pd.Timestamp.now(tz="UTC")

        # 计算持仓K线数（按小时）
        hours_held = (now - entry_time).total_seconds() / 3600
        bars_held = int(hours_held / self.api.timeframe.hours)

        if bars_held >= max_bars:
            log.warning(
                f"⏰ {inst_id} 持仓超时 ({bars_held} bars >= {max_bars}), 强制平仓"
            )
            try:
                side = "close_long" if position.side == "long" else "close_short"
                await self.api.place_order(OrderRequest(
                    inst_id=inst_id,
                    side=side,
                    size=position.size,
                    reduce_only=True,
                ))
                # 平仓后清除追踪
                self._position_entries.pop(inst_id, None)
            except Exception as e:
                log.error(f"Failed to close expired position {inst_id}: {e}")

    async def monitor_forever(self):
        """等待监控循环结束（兼容 main.py CLI 模式）"""
        try:
            if self._monitor_task:
                await self._monitor_task
        except asyncio.CancelledError:
            pass


# ============================================================
# 便捷函数
# ============================================================

def create_realtime_api(config: dict | None = None) -> OKXRealtimeAPI:
    """创建实时API"""
    return OKXRealtimeAPI(config)


async def start_live_trading(
    symbols: list[str] | None = None,
    signal_callback=None,
    risk_config: dict | None = None,
) -> LiveSignalMonitor:
    """启动实盘交易监控"""
    api = create_realtime_api()
    monitor = LiveSignalMonitor(api, signal_callback, risk_config)

    if await monitor.start():
        return monitor
    else:
        raise ConnectionError("Failed to start live trading")


# ============================================================
# 断网恢复
# ============================================================

async def restore_from_gap(
    symbols: list[str],
    data_store: RealtimeDataStore,
    api: OKXRealtimeAPI,
) -> dict[str, int]:
    """
    从数据缺口恢复
    系统启动时调用，填补离线期间的数据
    """
    results = {}

    for symbol in symbols:
        log.info(f"Checking data gap for {symbol}...")
        count = api.sync_from_api(symbol)
        results[symbol] = count
        if count > 0:
            log.info(f"Restored {count} bars for {symbol}")

    # 保存所有数据
    data_store.save_all()

    return results
