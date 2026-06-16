"""
OKX 合约信号系统 - 24小时自动调度器
每15分钟扫描配置币种，产出人工复核信号
"""
from __future__ import annotations

import logging
import asyncio
from datetime import datetime, timedelta, timezone
from threading import Event

import pandas as pd

from okx_signal_system.config import load_config
from okx_signal_system.data.loader import load_symbol_file
from okx_signal_system.ml.regime_adaptive import AdaptiveParamsManager
from okx_signal_system.paths import find_lightweight_history
from okx_signal_system.notification import feishu
from okx_signal_system.risk.model import Ledger, RiskConfig
from okx_signal_system.signal_service import SignalScanContext, SignalScanService
from okx_signal_system.signal_runtime import (
    DEFAULT_MAX_SIGNAL_LAG_MINUTES,
)
from okx_signal_system.strategy.trend_breakout import StrategyParams
from okx_signal_system.timeframe import timeframe_spec

log = logging.getLogger(__name__)

BEIJING_TZ = timezone(timedelta(hours=8), "Asia/Shanghai")

SCAN_INTERVAL_SECONDS = 15 * 60  # 15分钟
STATUS_INTERVAL_SECONDS = 30 * 60  # 30分钟状态推送
GLOBAL_INITIAL_EQUITY = 10000.0  # 兼容历史风险配置；正式输出不展示账户资金
DEFAULT_DATASET = "okx_15m_extended"
DEFAULT_SIGNAL_TIMEFRAME = "15m"
DEFAULT_TREND_TIMEFRAME = "1h"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _now_beijing() -> datetime:
    return datetime.now(BEIJING_TZ)


def _data_defaults() -> tuple[str, str, str]:
    try:
        cfg = load_config("base.yaml")
        data_cfg = cfg.get("data", {})
        dataset = str(data_cfg.get("historical_dataset", DEFAULT_DATASET))
        signal_timeframe = timeframe_spec(data_cfg.get("timeframe", DEFAULT_SIGNAL_TIMEFRAME)).key
        trend_timeframe = timeframe_spec(data_cfg.get("trend_timeframe", DEFAULT_TREND_TIMEFRAME)).key
        return dataset, signal_timeframe, trend_timeframe
    except Exception:
        log.warning("failed to load data defaults; using 15m defaults")
        return DEFAULT_DATASET, DEFAULT_SIGNAL_TIMEFRAME, DEFAULT_TREND_TIMEFRAME


def load_symbols_for_scan(dataset: str = DEFAULT_DATASET) -> list[str]:
    """加载配置中指定的币种列表"""
    try:
        cfg = load_config("base.yaml")
        return cfg.get("data", {}).get("symbols", ["BTC-USDT-SWAP"])
    except Exception:
        log.warning("无法加载配置，使用默认BTC")
        return ["BTC-USDT-SWAP"]


def symbol_to_inst_id(symbol: str) -> str:
    """将 BTC-USDT-SWAP 转换为 BTC_USDT_USDT（匹配实际文件名）"""
    symbol_clean = symbol.replace("-", "_").replace("_SWAP", "")
    # 实际文件名格式是 BTC_USDT_USDT_<timeframe>.parquet，需要两个 USDT
    if symbol_clean.count("USDT") == 1:
        symbol_clean = symbol_clean + "_USDT"
    return symbol_clean


def symbol_to_parquet_filename(symbol: str, timeframe: str = DEFAULT_SIGNAL_TIMEFRAME) -> str:
    """将 BTC-USDT-SWAP 转换为 BTC_USDT_USDT_<timeframe>.parquet"""
    return f"{symbol_to_inst_id(symbol)}_{timeframe_spec(timeframe).file_suffix}.parquet"


def inst_id_to_parquet_filename(inst_id: str, timeframe: str = DEFAULT_SIGNAL_TIMEFRAME) -> str:
    """将 BTC_USDT_USDT 转换为 BTC_USDT_USDT_<timeframe>.parquet"""
    return f"{inst_id}_{timeframe_spec(timeframe).file_suffix}.parquet"


def scan_single_symbol(
    inst_id: str,
    ledger: Ledger,
    params: StrategyParams,
    *,
    dataset: str = DEFAULT_DATASET,
    signal_timeframe: str = DEFAULT_SIGNAL_TIMEFRAME,
    trend_timeframe: str = DEFAULT_TREND_TIMEFRAME,
) -> dict | None:
    """扫描单个币种，返回信号或None"""
    results, _ = run_scan_cycle(
        [inst_id],
        ledger,
        params,
        dataset=dataset,
        signal_timeframe=signal_timeframe,
        trend_timeframe=trend_timeframe,
    )
    return results[0] if results else None


def run_scan_cycle(
    symbols: list[str],
    ledger: Ledger,
    params: StrategyParams,
    *,
    dataset: str = DEFAULT_DATASET,
    signal_timeframe: str = DEFAULT_SIGNAL_TIMEFRAME,
    trend_timeframe: str = DEFAULT_TREND_TIMEFRAME,
) -> tuple[list[dict], Ledger]:
    """执行一次完整扫描，返回信号列表和兼容账本"""
    signal_timeframe = timeframe_spec(signal_timeframe).key
    trend_timeframe = timeframe_spec(trend_timeframe).key

    async def candle_loader(inst_id: str, limit: int) -> pd.DataFrame:
        root = find_lightweight_history(dataset)
        path = root / inst_id_to_parquet_filename(inst_id, signal_timeframe)
        if not path.exists():
            log.warning("数据文件不存在: %s", path)
            return pd.DataFrame()
        return load_symbol_file(path).frame.tail(limit).reset_index(drop=True)

    service = SignalScanService(
        candle_loader=candle_loader,
        regime_manager=AdaptiveParamsManager(),
    )
    context = SignalScanContext(
        dataset=dataset,
        signal_timeframe=signal_timeframe,
        trend_timeframe=trend_timeframe,
        strategy_params=params,
        risk_config=RiskConfig(initial_equity=GLOBAL_INITIAL_EQUITY),
        ledger=ledger,
        quality_gate_allows_push=True,
        min_vote_approval_rate=0.4,
        mode="scheduler_signal_only",
        min_history_bars=100,
        max_signal_lag_minutes=DEFAULT_MAX_SIGNAL_LAG_MINUTES,
    )
    try:
        scan_result = asyncio.run(service.scan_cycle([symbol_to_inst_id(symbol) for symbol in symbols], context))
    except Exception as e:
        log.error("扫描周期失败: %s", e)
        return [], ledger

    ready_results = [
        {
            "inst_id": candidate.inst_id,
            "signal": candidate.signal,
            "decision": candidate.decision,
            "candidate": candidate,
            "payload": candidate.payload,
            "ts": _now_utc().isoformat(),
        }
        for candidate in scan_result.selection.tier_a
    ]
    if not ready_results:
        log.info("本轮扫描无有效信号")
    else:
        log.info(f"本轮扫描产出 {len(ready_results)} 个有效信号")

    if scan_result.selection.tier_b:
        log.info("本轮保留 %s 个B级候选", len(scan_result.selection.tier_b))
    if scan_result.selection.tier_c:
        log.info("本轮观察 %s 个C级候选", len(scan_result.selection.tier_c))
    return ready_results, ledger


def format_signal_summary(signals: list[dict]) -> str:
    """格式化信号摘要用于推送"""
    if not signals:
        return "无信号"
    lines = [f"📊 扫描时间: {_now_beijing().strftime('%Y-%m-%d %H:%M:%S')} 北京时间"]
    lines.append(f"有效信号数: {len(signals)}")
    for s in signals:
        d = s["decision"]
        sig = s["signal"]
        rr = d.risk_reward_ratio if d.risk_reward_ratio is not None else sig.risk_reward_ratio
        score = d.signal_score if d.signal_score is not None else sig.signal_score
        lines.append(
            f"✅ {s['inst_id']}: "
            f"方向={sig.side} | "
            f"评分={float(score or 0):.1f} | "
            f"目标盈亏比={float(rr or 0):.2f}R | "
            f"分析目标={float(sig.take_profit or 0):.4f}"
        )
    return "\n".join(lines)


def format_status_message(ledger: Ledger, cycle_count: int) -> str:
    """格式化状态消息"""
    return (
        f"🔔 系统状态报告\n"
        f"时间: {_now_beijing().strftime('%Y-%m-%d %H:%M:%S')} 北京时间\n"
        f"扫描周期: #{cycle_count}\n"
        f"模式: SIGNAL_ONLY\n"
        f"状态: {ledger.status}\n"
        f"说明: 只做信号研究和人工复核通知"
    )


class SignalScheduler:
    """信号调度器 - 24小时自动运行"""

    def __init__(
        self,
        dataset: str | None = None,
        params: StrategyParams | None = None,
        signal_timeframe: str | None = None,
        trend_timeframe: str | None = None,
        status_callback=None,
    ):
        default_dataset, default_signal_timeframe, default_trend_timeframe = _data_defaults()
        self.dataset = dataset or default_dataset
        self.params = params or StrategyParams()
        self.signal_timeframe = timeframe_spec(signal_timeframe or default_signal_timeframe).key
        self.trend_timeframe = timeframe_spec(trend_timeframe or default_trend_timeframe).key
        self.status_callback = status_callback
        self._stop_event = Event()
        self._cycle = 0
        self._ledger = Ledger(
            inst_id="GLOBAL",
            init_capital=GLOBAL_INITIAL_EQUITY,
            equity=GLOBAL_INITIAL_EQUITY,
        )
        self._symbols = load_symbols_for_scan(dataset)

    def stop(self):
        self._stop_event.set()

    def is_running(self) -> bool:
        return not self._stop_event.is_set()

    def run_cycle(self) -> list[dict]:
        self._cycle += 1
        log.info(f"=== 扫描周期 #{self._cycle} ===")
        results, self._ledger = run_scan_cycle(
            self._symbols,
            self._ledger,
            self.params,
            dataset=self.dataset,
            signal_timeframe=self.signal_timeframe,
            trend_timeframe=self.trend_timeframe,
        )

        # 推送信号到飞书
        if results:
            for r in results:
                if r.get("candidate") and r["candidate"].tier != "A":
                    continue
                sig = r["signal"]
                feishu.feishu_send_signal_card(
                    inst_id=r["inst_id"],
                    direction=sig.side,
                    entry_price=sig.entry_ref or 0,
                    stop_loss=sig.stop_loss or 0,
                    take_profit=sig.take_profit or 0,
                    reason=sig.reject_reason or "信号有效",
                )

        # 每30分钟推送状态到飞书
        if self._cycle % 2 == 0:
            feishu.feishu_send_status_card(
                status=self._ledger.status,
                cycle_count=self._cycle,
                last_signal_count=sum(1 for r in results if r.get("candidate") and r["candidate"].tier == "A"),
            )

        if self.status_callback:
            msg = format_status_message(self._ledger, self._cycle)
            try:
                self.status_callback(msg)
            except Exception as e:
                log.error(f"状态推送失败: {e}")
        return results

    def run_forever(self):
        """主循环：每15分钟扫描一次，24小时运行"""
        log.info(
            "scheduler started: dataset=%s signal_tf=%s trend_tf=%s symbols=%s interval=%sm",
            self.dataset,
            self.signal_timeframe,
            self.trend_timeframe,
            len(self._symbols),
            SCAN_INTERVAL_SECONDS // 60,
        )
        while not self._stop_event.is_set():
            try:
                self.run_cycle()
            except Exception as e:
                log.error(f"扫描周期异常: {e}")
            if self._stop_event.is_set():
                break
            next_run = _now_utc().timestamp() + SCAN_INTERVAL_SECONDS
            log.info(f"下次扫描: {datetime.fromtimestamp(next_run, tz=timezone.utc).strftime('%H:%M:%S')}")
            self._stop_event.wait(timeout=SCAN_INTERVAL_SECONDS)

    def run_once(self) -> list[dict]:
        """单次运行（用于测试）"""
        return self.run_cycle()


def run_live_scan():
    """命令行启动实时扫描"""
    import argparse

    parser = argparse.ArgumentParser(description="OKX信号系统实时扫描")
    parser.add_argument("--dataset", default=None, help="数据集名称")
    parser.add_argument("--signal-timeframe", default=None)
    parser.add_argument("--trend-timeframe", default=None)
    parser.add_argument("--once", action="store_true", help="单次扫描")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    scheduler = SignalScheduler(
        dataset=args.dataset,
        signal_timeframe=args.signal_timeframe,
        trend_timeframe=args.trend_timeframe,
    )
    if args.once:
        results = scheduler.run_once()
        if results:
            print(format_signal_summary(results))
        else:
            print("无有效信号")
    else:
        scheduler.run_forever()
