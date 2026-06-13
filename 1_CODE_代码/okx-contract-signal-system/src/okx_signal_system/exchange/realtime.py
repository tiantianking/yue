"""
OKX 合约信号系统 - 实时交易所API模块
实时获取市场数据，自动下单 + 数据持久化
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import threading
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

log = logging.getLogger(__name__)

# 缓存配置
CACHE_TTL_SECONDS = 5  # 缓存5秒
RECONNECT_DELAY = 3  # 重连延迟3秒
AI_PERSIST_INTERVAL = 60  # 每60秒写入磁盘


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

    def __init__(self, data_dir: Path | None = None):
        if data_dir is None:
            data_dir = find_lightweight_history("okx_1h_extended")
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # 内存缓存: {inst_id: DataFrame}
        self._cache: dict[str, pd.DataFrame] = {}
        # 最后写入时间
        self._last_write: dict[str, datetime] = {}

    def _get_file_path(self, inst_id: str) -> Path:
        """获取文件路径"""
        normalized = inst_id.replace("-", "_").replace("_SWAP", "").upper()
        # BTC-USDT-SWAP -> BTC_USDT_USDT_1h.parquet
        if normalized.count("USDT") == 1:
            normalized = f"{normalized}_USDT"
        return self.data_dir / f"{normalized}_1h.parquet"

    def load(self, inst_id: str) -> pd.DataFrame:
        """加载数据"""
        if inst_id in self._cache:
            return self._cache[inst_id]

        path = self._get_file_path(inst_id)
        if path.exists():
            df = pd.read_parquet(path)
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
        }])

        # 去重 + 追加
        if len(df) > 0 and new_row["ts"].iloc[0] == df["ts"].iloc[-1]:
            df.iloc[-1] = new_row.iloc[0]
        else:
            df = pd.concat([df, new_row], ignore_index=True)

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
                existing = pd.read_parquet(path)
                existing["ts"] = pd.to_datetime(existing["ts"], utc=True)
                df_combined = pd.concat([existing, df], ignore_index=True)
                df_combined = df_combined.drop_duplicates(subset=["ts"], keep="last")
                df_combined = df_combined.sort_values("ts").reset_index(drop=True)
                df_combined.to_parquet(path, index=False)
                self._cache[inst_id] = df_combined
            else:
                df.to_parquet(path, index=False)

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

    def __init__(self, on_candle: Callable[[str, dict], None] | None = None,
                 on_ticker: Callable[[str, dict], None] | None = None):
        self._ws: Any | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._subscriptions: set[str] = set()
        self._reconnect_count = 0
        self._max_reconnect = 10

        # 回调函数
        self.on_candle = on_candle
        self.on_ticker = on_ticker

        # 数据缓存
        self._candle_cache: dict[str, dict] = {}

    def _get_wss_url(self) -> str:
        """获取WebSocket URL（根据模拟/实盘模式自动切换）"""
        import os
        is_simulated = os.environ.get("OKX_IS_SIMULATED", "true").lower() != "false"
        if is_simulated:
            # OKX 模拟盘 WebSocket 地址
            return "wss://wspap.okx.com:8443/ws/v5/public"
        else:
            # OKX 实盘 WebSocket 地址
            return "wss://ws.okx.com:8443/ws/v5/public"

    def connect(self, subscriptions: list[str]) -> bool:
        """连接WebSocket"""
        if self._running:
            self.disconnect()

        self._subscriptions = set(subscriptions)
        self._running = True
        self._reconnect_count = 0

        try:
            self._start_websocket()
            return True
        except Exception as e:
            log.error(f"WebSocket connect error: {e}")
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
                self._handle_message(data)
            except Exception as e:
                log.error(f"WebSocket message error: {e}")

        def on_error(ws, error):
            log.error(f"WebSocket error: {error}")

        def on_close(ws, close_status_code, close_msg):
            log.warning(f"WebSocket closed: {close_status_code} {close_msg}")
            self._handle_disconnect()

        def on_open(ws):
            log.info("WebSocket connected")
            self._subscribe_channels()

        self._ws = websocket.WebSocketApp(
            url,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
            on_open=on_open,
        )

        self._thread = threading.Thread(target=self._ws.run_forever, daemon=True)
        self._thread.start()

    def _subscribe_channels(self):
        """订阅频道"""
        if not self._ws or not self._running:
            return

        # 订阅K线数据
        for inst_id in self._subscriptions:
            # 1小时K线（与配置一致）
            args = [{
                "channel": "candle1H",
                "instId": inst_id,
            }]
            msg = {
                "op": "subscribe",
                "args": args
            }
            self._ws.send(json.dumps(msg))
            log.info(f"Subscribed: {inst_id} candle1H")

        # 订阅ticker数据
        for inst_id in self._subscriptions:
            args = [{
                "channel": "tickers",
                "instId": inst_id,
            }]
            msg = {
                "op": "subscribe",
                "args": args
            }
            self._ws.send(json.dumps(msg))
            log.info(f"Subscribed: {inst_id} tickers")

    def _handle_message(self, data: dict):
        """处理接收到的消息"""
        if "event" in data:
            return  # 订阅确认等事件

        if "data" not in data:
            return

        arg = data.get("arg", {})
        channel = arg.get("channel", "")
        inst_id = arg.get("instId", "")
        items = data.get("data", [])

        if channel == "candle1H":
            for item in items:
                # K线格式: [ts, open, high, low, close, vol, volCcy, volCcyQuote, confirm]
                candle = {
                    "ts": item[0],
                    "open": float(item[1]),
                    "high": float(item[2]),
                    "low": float(item[3]),
                    "close": float(item[4]),
                    "volume": float(item[5]),
                    "quote_volume": float(item[7]) if len(item) > 7 and item[7] not in (None, "") else None,
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
        """处理断连 — 使用迭代重连循环，避免递归栈溢出"""
        while self._running and self._reconnect_count <= self._max_reconnect:
            self._reconnect_count += 1

            if self._reconnect_count > self._max_reconnect:
                log.error(f"Max reconnect ({self._max_reconnect}) reached, giving up")
                self._running = False
                return

            log.info(f"Reconnecting in {RECONNECT_DELAY}s... ({self._reconnect_count}/{self._max_reconnect})")
            time.sleep(RECONNECT_DELAY)

            try:
                self._start_websocket()
                return  # 重连成功 → 退出
            except Exception as e:
                log.error(f"Reconnect attempt {self._reconnect_count} failed: {e}")
                # 继续循环尝试

        # 如果 _running 被外部设为 False 但尚未标记，补标记
        if self._running:
            self._running = False

    def disconnect(self):
        """断开连接"""
        self._running = False
        if self._ws:
            self._ws.close()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

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
        self.config = config or {}
        self._connected = False
        self._ws_client: OKXWebSocketClient | None = None
        self._data_store = RealtimeDataStore()

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
        log.info(f"Connecting to OKX WebSocket for {len(symbols)} symbols...")

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
        )

        success = self._ws_client.connect(symbols)
        self._connected = success

        if success:
            log.info(f"OKX WebSocket connected, watching {len(symbols)} symbols")

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
        return self._connected and self._ws_client is not None

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

    async def get_candles(self, inst_id: str, bar: str = "1h", limit: int = 100) -> pd.DataFrame:
        """获取K线数据（优先本地，必要时从API补数据）"""
        # 先尝试本地数据
        local = self._data_store.load(inst_id)

        if len(local) >= limit:
            return local.tail(limit).reset_index(drop=True)

        # 本地数据不足，从API获取
        try:
            from okx_signal_system.exchange.okx import get_candles as okx_get_candles
            raw_bars = okx_get_candles(inst_id, bar=bar, limit=limit)

            if raw_bars:
                df = okx_candles_to_frame(raw_bars)

                # 合并到本地
                if len(local) > 0:
                    combined = pd.concat([local, df], ignore_index=True)
                    combined = combined.drop_duplicates(subset=["ts"], keep="last")
                    combined = combined.sort_values("ts").reset_index(drop=True)
                    self._data_store._cache[inst_id] = combined
                else:
                    self._data_store._cache[inst_id] = df

                return df.tail(limit).reset_index(drop=True)
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
            raw_bars = get_candles(inst_id, bar="1H", limit=300)
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
        from okx_signal_system.training.startup_quality import load_selected_strategy_params
        self._regime_mgr = AdaptiveParamsManager()
        self._strategy_params = load_selected_strategy_params()
        self._quality_gate_allows_push = False

        # --- 持仓追踪 (max_hold_bars) ---
        self._position_entries: dict[str, tuple[pd.Timestamp, StrategyParams]] = {}

    async def start(self):
        """启动监控"""
        if not await self.api.connect():
            log.error("Failed to connect to OKX")
            return False

        self._running = True
        log.info("Live signal monitor started")

        try:
            from okx_signal_system.training.startup_quality import run_startup_quality_gate
            report = run_startup_quality_gate(symbols=self.api._watched_symbols or None, max_symbols=None)
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

    async def _monitor_loop(self):
        """监控循环 — 信号生成 + 风控 + 环境自适应 + 持仓超时"""
        PREV_SIGNAL_COOLDOWN = 300  # 同一币种信号冷却5分钟
        _last_signal_time: dict[str, float] = {}

        while self._running:
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
                        continue

                    # 冷却期
                    if inst_id in _last_signal_time and time.time() - _last_signal_time[inst_id] < PREV_SIGNAL_COOLDOWN:
                        continue

                    # 获取K线数据
                    df = await self.api.get_candles(inst_id, bar="1h", limit=200)
                    if len(df) < 50:
                        continue
                    from okx_signal_system.training.startup_quality import is_latest_bar_fresh
                    if not is_latest_bar_fresh(df, max_lag_hours=3.0):
                        log.warning(f"{inst_id} latest candle is stale; waiting for live data")
                        continue

                    # 构建特征帧
                    strategy_params = self._strategy_params
                    features = build_feature_frame(
                        df,
                        fast_ema=strategy_params.fast_ema,
                        slow_ema=strategy_params.slow_ema,
                        breakout_window=strategy_params.breakout_window,
                        atr_window=strategy_params.atr_window,
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
                        continue

                    from okx_signal_system.strategy.ensemble import ensemble_vote
                    ensemble_result = ensemble_vote(
                        latest_row,
                        strategy_params,
                        features,
                        len(features) - 1,
                        base_score=signal.signal_score or 5.0,
                    )
                    effective_score = ensemble_result.final_score
                    if ensemble_result.final_side == "flat":
                        effective_score = max(1.0, effective_score - 3.0)
                    elif ensemble_result.final_side != signal.side:
                        effective_score = max(1.0, effective_score - 1.5)
                    penalty = self._regime_mgr.get_score_penalty()
                    if penalty < 0:
                        effective_score = max(1.0, effective_score + penalty)

                    # 风控校验
                    ledger = apply_halt_policy(self._ledger, self._risk_cfg)
                    risk_cfg = replace(
                        self._risk_cfg,
                        max_leverage=max(1.0, min(10.0, self._risk_cfg.max_leverage * self._regime_mgr.get_leverage_factor())),
                    )
                    signal = replace(signal, signal_score=effective_score)
                    decision = validate_signal(signal, ledger, risk_cfg)

                    if decision.accepted and effective_score >= 6.0 and self._quality_gate_allows_push:
                        _last_signal_time[inst_id] = time.time()
                        log.info(
                            f"✅ SIGNAL: {inst_id} {signal.side.upper()} "
                            f"@ {signal.entry_ref:.2f} | "
                            f"score={decision.signal_score:.0f}/10 | "
                            f"regime={self._regime_mgr.get_regime_name_cn()} | "
                            f"RR={decision.risk_reward_ratio:.1f}:1 | "
                            f"lev={decision.leverage_used:.1f}x"
                        )
                        # 回调通知
                        if self.signal_callback:
                            try:
                                self.signal_callback(signal, decision)
                            except Exception as cb_err:
                                log.error(f"Signal callback error: {cb_err}")
                        # 内嵌飞书推送（callback 未设置时的 fallback）
                        else:
                            try:
                                from okx_signal_system.notify.feishu import send_signal_alert
                                send_signal_alert(
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
                                    max_loss_pct=getattr(decision, 'max_loss_pct', None),
                                    margin_loss_pct=getattr(decision, 'margin_loss_pct', None),
                                )
                                log.info(f"飞书推送(fallback): {inst_id} {signal.side}")
                            except Exception as feishu_err:
                                log.error(f"飞书推送失败: {feishu_err}")

                # 持久化
                self.api.persist_data()

                await asyncio.sleep(10)

            except Exception as e:
                log.error(f"Monitor error: {e}")
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
        bars_held = int(hours_held)

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
