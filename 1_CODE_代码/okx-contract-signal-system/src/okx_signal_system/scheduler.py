"""
OKX 合约信号系统 - 24小时自动调度器
每15分钟扫描15个币种，产出交易信号
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from threading import Event

import pandas as pd

from okx_signal_system.config import load_config
from okx_signal_system.data.loader import load_symbol_file
from okx_signal_system.features.indicators import build_feature_frame
from okx_signal_system.paths import find_lightweight_history
from okx_signal_system.notification import feishu
from okx_signal_system.risk.model import Ledger, RiskConfig, validate_signal
from okx_signal_system.strategy.trend_breakout import StrategyParams, generate_signals, build_signal

log = logging.getLogger(__name__)

SCAN_INTERVAL_SECONDS = 15 * 60  # 15分钟
STATUS_INTERVAL_SECONDS = 30 * 60  # 30分钟状态推送
GLOBAL_INITIAL_EQUITY = 10000.0  # 总初始本金


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def load_symbols_for_scan(dataset: str = "okx_1h_extended") -> list[str]:
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
    # 实际文件名格式是 BTC_USDT_USDT_1h.parquet，需要两个 USDT
    if symbol_clean.count("USDT") == 1:
        symbol_clean = symbol_clean + "_USDT"
    return symbol_clean


def symbol_to_parquet_filename(symbol: str) -> str:
    """将 BTC-USDT-SWAP 转换为 BTC_USDT_USDT_1h.parquet"""
    return f"{symbol_to_inst_id(symbol)}_1h.parquet"


def inst_id_to_parquet_filename(inst_id: str) -> str:
    """将 BTC_USDT_USDT 转换为 BTC_USDT_USDT_1h.parquet"""
    return f"{inst_id}_1h.parquet"


def scan_single_symbol(
    inst_id: str, ledger: Ledger, params: StrategyParams
) -> dict | None:
    """扫描单个币种，返回信号或None"""
    try:
        root = find_lightweight_history("okx_1h_extended")
        fname = inst_id_to_parquet_filename(inst_id)
        path = root / fname
        if not path.exists():
            log.warning(f"数据文件不存在: {path}")
            return None
        symbol_data = load_symbol_file(path)
        frame = symbol_data.frame
        if len(frame) < 100:
            log.warning(f"{inst_id} 数据不足")
            return None
        features = build_feature_frame(
            frame,
            fast_ema=params.fast_ema,
            slow_ema=params.slow_ema,
            breakout_window=params.breakout_window,
            atr_window=params.atr_window,
        )
        signals = generate_signals(features, inst_id=inst_id, params=params)
        accepted = [s for s in signals if s.accepted]
        if not accepted:
            return None
        signal = accepted[-1]
        risk_config = RiskConfig(initial_equity=GLOBAL_INITIAL_EQUITY)
        decision = validate_signal(signal, ledger, risk_config)
        return {
            "inst_id": inst_id,
            "signal": signal,
            "decision": decision,
            "ts": _now_utc().isoformat(),
        }
    except Exception as e:
        log.error(f"扫描 {inst_id} 失败: {e}")
        return None


def run_scan_cycle(symbols: list[str], ledger: Ledger, params: StrategyParams) -> tuple[list[dict], Ledger]:
    """执行一次完整扫描，返回信号列表和更新后的账本"""
    results: list[dict] = []
    for symbol in symbols:
        inst_id = symbol_to_parquet_filename(symbol).replace("_1h.parquet", "")
        result = scan_single_symbol(inst_id, ledger, params)
        if result and result["decision"].accepted:
            results.append(result)
    if not results:
        log.info("本轮扫描无有效信号")
        return [], ledger
    log.info(f"本轮扫描产出 {len(results)} 个有效信号")
    return results, ledger


def format_signal_summary(signals: list[dict]) -> str:
    """格式化信号摘要用于推送"""
    if not signals:
        return "无信号"
    lines = [f"📊 扫描时间: {_now_utc().strftime('%Y-%m-%d %H:%M:%S')} UTC"]
    lines.append(f"有效信号数: {len(signals)}")
    for s in signals:
        d = s["decision"]
        sig = s["signal"]
        lines.append(
            f"✅ {s['inst_id']}: "
            f"方向={sig.side} | "
            f"qty={d.qty:.4f} | "
            f"杠杆={d.leverage_used:.1f}x | "
            f"止盈={sig.take_profit:.4f}"
        )
    return "\n".join(lines)


def format_status_message(ledger: Ledger, cycle_count: int) -> str:
    """格式化状态消息"""
    return (
        f"🔔 系统状态报告\n"
        f"时间: {_now_utc().strftime('%Y-%m-%d %H:%M:%S')} UTC\n"
        f"扫描周期: #{cycle_count}\n"
        f"账户 equity: ${ledger.equity:.2f}\n"
        f"持仓数: {ledger.open_positions}\n"
        f"状态: {ledger.status}\n"
        f"连续亏损: {ledger.loss_streak}\n"
        f"最大回撤: {ledger.max_drawdown:.2%}"
    )


class SignalScheduler:
    """信号调度器 - 24小时自动运行"""

    def __init__(
        self,
        dataset: str = "okx_1h_extended",
        params: StrategyParams | None = None,
        status_callback=None,
    ):
        self.dataset = dataset
        self.params = params or StrategyParams()
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
        results, self._ledger = run_scan_cycle(self._symbols, self._ledger, self.params)

        # 推送信号到飞书
        if results:
            for r in results:
                sig = r["signal"]
                dec = r["decision"]
                feishu.feishu_send_signal_card(
                    inst_id=r["inst_id"],
                    direction=sig.side,
                    qty=dec.qty or 0,
                    leverage=dec.leverage_used or 0,
                    entry_price=sig.entry_ref or 0,
                    stop_loss=sig.stop_loss or 0,
                    take_profit=sig.take_profit or 0,
                    reason=sig.reject_reason or "信号有效",
                )

        # 每30分钟推送状态到飞书
        if self._cycle % 2 == 0:
            feishu.feishu_send_status_card(
                equity=self._ledger.equity,
                open_positions=self._ledger.open_positions,
                status=self._ledger.status,
                loss_streak=self._ledger.loss_streak,
                max_drawdown=self._ledger.max_drawdown,
                cycle_count=self._cycle,
                last_signal_count=len(results),
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
        log.info(f"调度器启动，监控 {len(self._symbols)} 个币种，每 {SCAN_INTERVAL_SECONDS // 60} 分钟扫描")
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
    parser.add_argument("--dataset", default="okx_1h_extended", help="数据集名称")
    parser.add_argument("--once", action="store_true", help="单次扫描")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    scheduler = SignalScheduler(dataset=args.dataset)
    if args.once:
        results = scheduler.run_once()
        if results:
            print(format_signal_summary(results))
        else:
            print("无有效信号")
    else:
        scheduler.run_forever()