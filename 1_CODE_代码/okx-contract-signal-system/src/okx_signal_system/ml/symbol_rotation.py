"""
OKX 合约信号系统 - 智能币种轮换策略
根据市场表现自动切换关注的币种
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import pandas as pd

from okx_signal_system.strategy.trend_breakout import StrategyParams

log = logging.getLogger(__name__)

# 币种轮换配置
MAX_ACTIVE_SYMBOLS = 5  # 最多同时关注5个币种
MIN_TRADES_FOR_SWITCH = 10  # 最少交易次数
SWITCH_THRESHOLD_PF = 1.2  # PF门槛
SWITCH_THRESHOLD_WR = 0.35  # 胜率门槛
RE_EVAL_INTERVAL = 24  # 每24小时重新评估


@dataclass
class SymbolPerformance:
    """币种表现"""
    inst_id: str
    total_trades: int = 0
    winning_trades: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    total_return: float = 0.0
    avg_return_per_trade: float = 0.0
    max_drawdown: float = 0.0
    last_trade_time: datetime | None = None
    score: float = 0.0

    def is_active(self) -> bool:
        """是否活跃"""
        return self.total_trades >= MIN_TRADES_FOR_SWITCH

    def is_profitable(self) -> bool:
        """是否盈利"""
        return self.profit_factor >= SWITCH_THRESHOLD_PF and self.win_rate >= SWITCH_THRESHOLD_WR


@dataclass
class SymbolRotationConfig:
    """币种轮换配置"""
    max_active: int = MAX_ACTIVE_SYMBOLS
    min_trades: int = MIN_TRADES_FOR_SWITCH
    pf_threshold: float = SWITCH_THRESHOLD_PF
    wr_threshold: float = SWITCH_THRESHOLD_WR
    re_eval_hours: int = RE_EVAL_INTERVAL


@dataclass
class RotationDecision:
    """轮换决策"""
    add_symbols: list[str]  # 新增币种
    remove_symbols: list[str]  # 移除币种
    maintain_symbols: list[str]  # 保持币种
    reason: str
    timestamp: datetime


class SymbolRotator:
    """
    智能币种轮换器
    功能：
    1. 追踪各币种表现
    2. 自动淘汰表现差的币种
    3. 筛选表现好的币种
    4. 动态调整关注列表
    """

    def __init__(
        self,
        available_symbols: list[str],
        data_dir: Path | str,
        config: SymbolRotationConfig | None = None,
    ):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.config = config or SymbolRotationConfig()
        self.available_symbols = available_symbols
        self.symbol_performance: dict[str, SymbolPerformance] = {}
        self.active_symbols: list[str] = []
        self.last_eval_time = datetime.now(timezone.utc)

        # 初始选择表现最好的币种
        self._initialize_active_symbols()

        self._load_state()

    def _initialize_active_symbols(self):
        """初始化活跃币种列表"""
        # 默认选择流动性好的主流币
        main_coins = ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP", "BNB-USDT-SWAP", "XRP-USDT-SWAP"]
        for coin in main_coins:
            if coin in self.available_symbols:
                self.active_symbols.append(coin)
        log.info(f"Initialized with {len(self.active_symbols)} active symbols")

    def _state_file(self) -> Path:
        return self.data_dir / "symbol_rotation_state.json"

    def _load_state(self):
        """加载状态"""
        path = self._state_file()
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                self.active_symbols = data.get("active_symbols", [])
                self.last_eval_time = pd.to_datetime(data.get("last_eval_time")).tz_localize("UTC")
                log.info("Loaded symbol rotation state")
            except Exception as e:
                log.warning(f"Failed to load state: {e}")

    def _save_state(self):
        """保存状态"""
        data = {
            "active_symbols": self.active_symbols,
            "last_eval_time": self.last_eval_time.isoformat(),
            "available_symbols": self.available_symbols,
        }
        self._state_file().write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")

    def update_symbol_performance(self, inst_id: str, trades: list[dict]):
        """更新币种表现"""
        if not trades:
            return

        pnls = [t.get("net_pnl", 0) for t in trades if t.get("net_pnl") is not None]
        if not pnls:
            return

        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]

        total_wins = sum(wins) if wins else 0
        total_losses = abs(sum(losses)) if losses else 0

        pf = total_wins / total_losses if total_losses > 0 else 0
        wr = len(wins) / len(pnls) if pnls else 0

        # 计算最大回撤
        equity = [0]
        for pnl in pnls:
            equity.append(equity[-1] + pnl)
        running_max = [0]
        for e in equity[1:]:
            running_max.append(max(running_max[-1], e))
        dd = 0
        for i in range(len(equity)):
            if running_max[i] > 0:
                dd = max(dd, (equity[i] - running_max[i]) / running_max[i])

        perf = SymbolPerformance(
            inst_id=inst_id,
            total_trades=len(pnls),
            winning_trades=len(wins),
            win_rate=wr,
            profit_factor=pf,
            total_return=sum(pnls),
            avg_return_per_trade=sum(pnls) / len(pnls),
            max_drawdown=dd,
            last_trade_time=datetime.now(timezone.utc),
            score=pf * 0.6 + wr * 0.4,  # 综合评分
        )

        self.symbol_performance[inst_id] = perf
        log.info(f"Updated performance for {inst_id}: PF={pf:.2f}, WR={wr:.1%}")

    def calculate_rankings(self) -> list[SymbolPerformance]:
        """计算币种排名"""
        performances = list(self.symbol_performance.values())
        # 过滤活跃的
        active = [p for p in performances if p.is_active()]
        # 按评分排序
        ranked = sorted(active, key=lambda x: x.score, reverse=True)
        return ranked

    def should_re_evaluate(self) -> bool:
        """判断是否需要重新评估"""
        elapsed = (datetime.now(timezone.utc) - self.last_eval_time).total_seconds()
        return elapsed > self.config.re_eval_hours * 3600

    def evaluate_and_rotate(self) -> RotationDecision:
        """评估并轮换币种"""
        self.last_eval_time = datetime.now(timezone.utc)

        ranked = self.calculate_rankings()

        if not ranked:
            return RotationDecision(
                add_symbols=[],
                remove_symbols=[],
                maintain_symbols=self.active_symbols,
                reason="No performance data",
                timestamp=self.last_eval_time,
            )

        # 淘汰名单：表现差或不活跃
        to_remove = []
        for sym in self.active_symbols:
            if sym not in self.symbol_performance:
                to_remove.append(sym)
            elif not self.symbol_performance[sym].is_active():
                to_remove.append(sym)
            elif not self.symbol_performance[sym].is_profitable():
                to_remove.append(sym)

        # 淘汰表现最差的币种
        if len(ranked) > self.config.max_active:
            # 找出不在前N名且在活跃列表中的
            top_symbols = {r.inst_id for r in ranked[:self.config.max_active]}
            for sym in self.active_symbols:
                if sym not in top_symbols and sym not in to_remove:
                    to_remove.append(sym)

        # 筛选新增币种
        top_symbols = {r.inst_id for r in ranked[:self.config.max_active]}
        to_add = [s for s in top_symbols if s not in self.active_symbols]

        # 更新活跃列表
        self.active_symbols = [
            s for s in self.active_symbols if s not in to_remove
        ] + to_add[:self.config.max_active - len(self.active_symbols)]

        self._save_state()

        return RotationDecision(
            add_symbols=to_add,
            remove_symbols=to_remove,
            maintain_symbols=[s for s in self.active_symbols if s not in to_add],
            reason="performance_based_rotation",
            timestamp=self.last_eval_time,
        )

    def get_active_symbols(self) -> list[str]:
        """获取当前活跃币种"""
        return self.active_symbols.copy()

    def get_top_symbols(self, n: int = 3) -> list[str]:
        """获取排名前N的币种"""
        ranked = self.calculate_rankings()
        return [r.inst_id for r in ranked[:n]]

    def get_symbol_report(self) -> dict:
        """获取币种报告"""
        report = {
            "active_symbols": self.active_symbols,
            "last_eval": self.last_eval_time.isoformat(),
            "rankings": [],
        }

        for perf in self.calculate_rankings():
            report["rankings"].append({
                "symbol": perf.inst_id,
                "trades": perf.total_trades,
                "win_rate": f"{perf.win_rate:.1%}",
                "profit_factor": f"{perf.profit_factor:.2f}",
                "return": f"${perf.total_return:.2f}",
                "score": f"{perf.score:.3f}",
            })

        return report


def create_rotator(
    available_symbols: list[str],
    data_dir: Path | str,
    config: SymbolRotationConfig | None = None,
) -> SymbolRotator:
    """创建币种轮换器"""
    return SymbolRotator(available_symbols, data_dir, config)