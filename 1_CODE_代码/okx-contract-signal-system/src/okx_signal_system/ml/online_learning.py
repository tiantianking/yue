"""
OKX 合约信号系统 - 在线学习与性能追踪模块
实时追踪交易表现，自动调整参数
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd

from okx_signal_system.strategy.trend_breakout import StrategyParams

log = logging.getLogger(__name__)

# 学习率参数
ADAPTATION_LEARNING_RATE = 0.05  # 每次调整5%
MIN_TRADES_FOR_ADAPTATION = 20  # 最少20笔交易才开始自适应
PERFORMANCE_WINDOW = 50  # 评估窗口

# 性能评分权重
WEIGHT_PF = 0.6  # 盈亏比权重60%
WEIGHT_WR = 0.2  # 胜率权重20%
WEIGHT_RETURN = 0.2  # 收益率权重20%
TARGET_RR_FLOOR = 6.0
MIN_FAST_EMA = 120
MIN_SLOW_EMA = 720
MAX_HOLD_BARS_CAP = 768


def _enforce_target_rr_floor(params: StrategyParams) -> StrategyParams:
    if params.take_profit_mult >= TARGET_RR_FLOOR:
        return params
    data = asdict(params)
    data["take_profit_mult"] = TARGET_RR_FLOOR
    return StrategyParams(**data)


@dataclass
class TradeRecord:
    """交易记录"""
    inst_id: str
    side: Literal["long", "short"]
    entry_time: datetime
    exit_time: datetime | None
    entry_price: float
    exit_price: float | None
    qty: float
    pnl: float | None
    pnl_pct: float | None
    exit_reason: str | None
    params: dict  # 当时使用的参数


@dataclass
class PerformanceMetrics:
    """性能指标"""
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    payoff_ratio: float = 0.0
    total_return: float = 0.0
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0
    score: float = 0.0  # 综合评分


@dataclass
class AdaptationResult:
    """参数自适应结果"""
    old_params: StrategyParams
    new_params: StrategyParams
    reason: str
    confidence: float  # 0-1
    metrics_before: PerformanceMetrics
    metrics_after: PerformanceMetrics


class OnlineLearningEngine:
    """
    在线学习引擎
    功能：
    1. 实时追踪交易表现
    2. 计算性能指标
    3. 自动调整策略参数
    4. 管理参数历史
    """

    def __init__(self, data_dir: Path | str):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.trades: list[TradeRecord] = []
        self.params_history: list[dict] = []
        self.current_params = StrategyParams()
        self.best_params = StrategyParams()
        self.best_score = 0.0

        self._load_state()

    def _state_file(self) -> Path:
        return self.data_dir / "online_learning_state.json"

    def _load_state(self):
        """加载保存的状态"""
        path = self._state_file()
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                self.current_params = _enforce_target_rr_floor(StrategyParams(**data.get("current_params", {})))
                self.best_params = _enforce_target_rr_floor(StrategyParams(**data.get("best_params", {})))
                self.best_score = data.get("best_score", 0.0)
                log.info("Loaded online learning state")
            except Exception as e:
                log.warning(f"Failed to load state: {e}")

    def _save_state(self):
        """保存状态"""
        data = {
            "current_params": asdict(self.current_params),
            "best_params": asdict(self.best_params),
            "best_score": self.best_score,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self._state_file().write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")

    def record_trade(self, trade: TradeRecord):
        """记录一笔交易"""
        self.trades.append(trade)
        self._save_trades()
        log.info(f"Trade recorded: {trade.inst_id} {trade.side} PnL={trade.pnl:.2f}")

    def _save_trades(self):
        """保存交易记录"""
        path = self.data_dir / "trade_history.json"
        data = [asdict(t) for t in self.trades]
        path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")

    def load_trades_from_csv(self, csv_path: Path | str):
        """从CSV加载历史交易"""
        df = pd.read_csv(csv_path)
        for _, row in df.iterrows():
            trade = TradeRecord(
                inst_id=row.get("inst_id", ""),
                side=row.get("side", "long"),
                entry_time=pd.to_datetime(row.get("entry_time")),
                exit_time=pd.to_datetime(row.get("exit_time")) if pd.notna(row.get("exit_time")) else None,
                entry_price=float(row.get("entry_price", 0)),
                exit_price=float(row.get("exit_price", 0)) if pd.notna(row.get("exit_price")) else None,
                qty=float(row.get("qty", 0)),
                pnl=float(row.get("net_pnl", 0)) if pd.notna(row.get("net_pnl")) else None,
                pnl_pct=float(row.get("pnl_pct", 0)) if pd.notna(row.get("pnl_pct")) else None,
                exit_reason=row.get("exit_reason"),
                params={},
            )
            self.trades.append(trade)

    def calculate_metrics(self, trades: list[TradeRecord] | None = None) -> PerformanceMetrics:
        """计算性能指标"""
        trades = trades or self.trades[-PERFORMANCE_WINDOW:]

        if not trades:
            return PerformanceMetrics()

        pnls = [t.pnl for t in trades if t.pnl is not None]
        if not pnls:
            return PerformanceMetrics()

        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]

        total_wins = sum(wins) if wins else 0
        total_losses = abs(sum(losses)) if losses else 0

        pf = total_wins / total_losses if total_losses > 0 else (float("inf") if total_wins > 0 else 0)
        wr = len(wins) / len(pnls) if pnls else 0
        avg_win = total_wins / len(wins) if wins else 0
        avg_loss = total_losses / len(losses) if losses else 0
        payoff = avg_win / abs(avg_loss) if avg_loss != 0 else 0

        # 计算最大回撤
        equity_curve = np.cumsum(pnls)
        running_max = np.maximum.accumulate(equity_curve)
        drawdowns = (equity_curve - running_max) / running_max
        max_dd = abs(min(drawdowns)) if len(drawdowns) > 0 else 0

        # 综合评分
        score = WEIGHT_PF * min(pf / 2.0, 1.0) + WEIGHT_WR * wr + WEIGHT_RETURN * min(sum(pnls) / 1000, 1.0)

        return PerformanceMetrics(
            total_trades=len(pnls),
            winning_trades=len(wins),
            losing_trades=len(losses),
            win_rate=wr,
            profit_factor=pf,
            avg_win=avg_win,
            avg_loss=avg_loss,
            payoff_ratio=payoff,
            total_return=sum(pnls),
            max_drawdown=max_dd,
            score=score,
        )

    def calculate_score(self, metrics: PerformanceMetrics) -> float:
        """计算综合评分"""
        pf_normalized = min(metrics.profit_factor / 2.0, 1.0)  # PF>2.0满分
        return WEIGHT_PF * pf_normalized + WEIGHT_WR * metrics.win_rate + WEIGHT_RETURN * min(metrics.total_return / 1000, 1.0)

    def should_adapt(self) -> bool:
        """判断是否应该调整参数"""
        return len(self.trades) >= MIN_TRADES_FOR_ADAPTATION

    def adapt_params(self) -> AdaptationResult | None:
        """
        自适应调整参数
        基于最近交易的性能表现，微调参数组合
        """
        if not self.should_adapt():
            return None

        recent_trades = self.trades[-PERFORMANCE_WINDOW:]
        metrics = self.calculate_metrics(recent_trades)
        old_params = self.current_params

        log.info(f"Adapting params. Score: {metrics.score:.3f}, Trades: {metrics.total_trades}")

        # 根据性能调整参数
        new_params_dict = asdict(old_params)

        # PF太低 -> 调整止损止盈倍数
        if metrics.profit_factor < 1.0:
            if metrics.win_rate < 0.4:
                # 胜率低，降低止损，提高盈亏比
                new_params_dict["atr_stop_mult"] = min(old_params.atr_stop_mult * 1.1, 4.5)
                new_params_dict["take_profit_mult"] = max(old_params.take_profit_mult * 0.95, TARGET_RR_FLOOR)
            else:
                # 盈亏比低，增加止盈
                new_params_dict["take_profit_mult"] = min(old_params.take_profit_mult * 1.1, 7.0)

        # PF太高但交易数少 -> 增加持仓时间
        if metrics.profit_factor > 1.5 and metrics.total_trades < 15:
            new_params_dict["max_hold_bars"] = min(old_params.max_hold_bars + 96, MAX_HOLD_BARS_CAP)

        # 回撤太大 -> 收紧止损
        if metrics.max_drawdown > 0.15:
            new_params_dict["atr_stop_mult"] = max(old_params.atr_stop_mult * 0.9, 1.0)

        # 趋势跟随 -> 调整EMA周期
        if metrics.win_rate < 0.35 and metrics.profit_factor > 1.2:
            # 低胜率高盈亏比 -> 趋势策略，缩短EMA周期
            new_params_dict["fast_ema"] = max(old_params.fast_ema - 5, MIN_FAST_EMA)
            new_params_dict["slow_ema"] = max(old_params.slow_ema - 10, MIN_SLOW_EMA)

        # 应用学习率限制调整幅度
        for key in new_params_dict:
            if key in asdict(old_params):
                old_val = asdict(old_params)[key]
                new_val = new_params_dict[key]
                if old_val != new_val:
                    # 限制单次调整幅度
                    diff = new_val - old_val
                    new_params_dict[key] = old_val + diff * ADAPTATION_LEARNING_RATE

        new_params_dict["take_profit_mult"] = max(
            float(new_params_dict.get("take_profit_mult", TARGET_RR_FLOOR)),
            TARGET_RR_FLOOR,
        )
        new_params = StrategyParams(**new_params_dict)

        # 更新最佳参数
        if metrics.score > self.best_score:
            self.best_score = metrics.score
            self.best_params = new_params

        self.current_params = new_params
        self._save_state()

        # 记录历史
        self.params_history.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "params": asdict(new_params),
            "metrics": asdict(metrics),
            "score": metrics.score,
        })

        return AdaptationResult(
            old_params=old_params,
            new_params=new_params,
            reason="performance_based_adaptation",
            confidence=min(metrics.total_trades / MIN_TRADES_FOR_ADAPTATION, 1.0),
            metrics_before=metrics,
            metrics_after=metrics,
        )

    def get_current_params(self) -> StrategyParams:
        """获取当前参数"""
        return self.current_params

    def get_best_params(self) -> StrategyParams:
        """获取最佳参数"""
        return self.best_params

    def get_performance_summary(self) -> dict:
        """获取性能摘要"""
        metrics = self.calculate_metrics()
        return {
            "total_trades": metrics.total_trades,
            "win_rate": f"{metrics.win_rate:.1%}",
            "profit_factor": f"{metrics.profit_factor:.2f}",
            "payoff_ratio": f"{metrics.payoff_ratio:.2f}",
            "total_return": f"${metrics.total_return:.2f}",
            "max_drawdown": f"{metrics.max_drawdown:.1%}",
            "score": f"{metrics.score:.3f}",
            "current_params": asdict(self.current_params),
            "best_score": f"{self.best_score:.3f}",
            "best_params": asdict(self.best_params),
        }


def create_learning_engine(data_dir: Path | str) -> OnlineLearningEngine:
    """创建学习引擎"""
    return OnlineLearningEngine(data_dir)
