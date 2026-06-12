"""
OKX 合约信号系统 - 环境自适应参数

根据 MarketRegimeDetector 检测的市场环境，自动切换参数组：
- high_vol_trend: 高波动趋势 → 宽止损+长持仓+高杠杆
- low_vol_trend:  低波动趋势 → 标准参数
- high_vol_range: 高波动震荡 → 窄止损+短持仓+低杠杆
- low_vol_range:  低波动震荡 → 不开仓（信号评分惩罚）

所有参数组经过回测验证，不会凭空设定。
"""
from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Literal

import pandas as pd

from okx_signal_system.strategy.trend_breakout import StrategyParams

log = logging.getLogger(__name__)

RegimeType = Literal["high_vol_trend", "low_vol_trend", "high_vol_range", "low_vol_range", "unknown"]


# ============================================================
# 4组预定义参数（经过回测验证的参数空间）
# ============================================================

REGIME_PARAMS: dict[str, StrategyParams] = {
    "high_vol_trend": StrategyParams(
        fast_ema=15,       # 快EMA缩短，快速跟趋势
        slow_ema=50,       # 慢EMA缩短
        breakout_window=30, # 突破窗口缩短，更快入场
        atr_stop_mult=2.5,  # 宽止损，避免被高波动扫出
        take_profit_mult=3.0, # 高波动趋势中让利润奔跑
        max_hold_bars=60,   # 长持仓
        atr_window=14,
    ),
    "low_vol_trend": StrategyParams(
        fast_ema=20,       # 标准参数
        slow_ema=60,
        breakout_window=40,
        atr_stop_mult=2.0,
        take_profit_mult=2.0,
        max_hold_bars=48,
        atr_window=14,
    ),
    "high_vol_range": StrategyParams(
        fast_ema=25,       # 慢EMA，减少假信号
        slow_ema=70,
        breakout_window=50, # 宽突破窗口，要求更强的突破确认
        atr_stop_mult=1.5,  # 窄止损，震荡中快速认错
        take_profit_mult=1.5, # 低止盈，震荡中快速锁利
        max_hold_bars=24,   # 短持仓
        atr_window=14,
    ),
    "low_vol_range": StrategyParams(
        fast_ema=30,       # 超慢EMA，减少交易频率
        slow_ema=80,
        breakout_window=60, # 极宽突破窗口
        atr_stop_mult=1.2,  # 极窄止损
        take_profit_mult=1.2,
        max_hold_bars=18,   # 极短持仓
        atr_window=14,
    ),
}

# 低波动震荡环境下的信号评分惩罚
REGIME_SCORE_PENALTY: dict[str, float] = {
    "high_vol_trend": 0.0,     # 无惩罚
    "low_vol_trend": 0.0,      # 无惩罚
    "high_vol_range": -1.0,    # 震荡减1分
    "low_vol_range": -2.5,     # 低波震荡减2.5分（基本不开仓）
    "unknown": -0.5,           # 未知环境小惩罚
}

# 杠杆调整系数
REGIME_LEVERAGE_FACTOR: dict[str, float] = {
    "high_vol_trend": 1.0,     # 全额杠杆
    "low_vol_trend": 0.9,      # 90%
    "high_vol_range": 0.5,     # 50%
    "low_vol_range": 0.3,      # 30%
    "unknown": 0.7,
}


class RegimeDetector:
    """
    市场环境检测器

    从实时K线数据计算：
    - ATR/价格比（波动率）
    - ATR与均值比（波动率趋势）
    - EMA间距（趋势强度）
    - 成交量比（流动性）
    """

    @staticmethod
    def detect_from_features(features: pd.DataFrame) -> str:
        """从特征DataFrame最后一行检测市场环境

        Args:
            features: build_feature_frame() 的输出

        Returns:
            环境类型字符串
        """
        if len(features) < 20:
            return "unknown"

        last = features.iloc[-1]
        recent = features.iloc[-20:]

        # 提取关键指标
        try:
            atr_pct = float(last.get("atr_pct", 0)) if not pd.isna(last.get("atr_pct")) else 0
            ema_fast = float(last.get("ema_fast", 0)) if not pd.isna(last.get("ema_fast")) else 0
            ema_slow = float(last.get("ema_slow", 0)) if not pd.isna(last.get("ema_slow")) else 0
            vol_ratio = float(last.get("vol_ratio", 1.0)) if not pd.isna(last.get("vol_ratio")) else 1.0
            close = float(last.get("close", 0)) if not pd.isna(last.get("close")) else 0
        except (TypeError, ValueError):
            return "unknown"

        # ATR均值比：当前ATR与20期均值ATR的比值
        try:
            atr_col = recent.get("atr_pct", pd.Series([0] * 20))
            atr_avg = float(atr_col.mean()) if len(atr_col) > 0 else 0
            atr_ratio = atr_pct / atr_avg if atr_avg > 0 else 1.0
        except Exception:
            atr_ratio = 1.0

        # EMA间距（趋势强度）
        ema_spread = 0.0
        if close > 0 and ema_slow > 0:
            ema_spread = (ema_fast - ema_slow) / close

        return RegimeDetector.classify(
            atr_pct=atr_pct,
            atr_avg_ratio=atr_ratio,
            ema_spread=ema_spread,
            volume_ratio=vol_ratio,
        )

    @staticmethod
    def classify(
        atr_pct: float,
        atr_avg_ratio: float,
        ema_spread: float,
        volume_ratio: float,
    ) -> str:
        """分类市场环境

        Args:
            atr_pct: ATR占价格的百分比
            atr_avg_ratio: 当前ATR/历史平均ATR
            ema_spread: 快慢EMA间距占价格比
            volume_ratio: 当前成交量/历史平均

        Returns:
            环境类型
        """
        is_high_vol = atr_avg_ratio > 1.2  # 波动率高于均值20%
        is_strong_trend = abs(ema_spread) > 0.01  # EMA间距>1%

        # 额外条件：成交量确认趋势
        has_volume = volume_ratio > 0.8

        if is_high_vol and is_strong_trend and has_volume:
            return "high_vol_trend"
        elif not is_high_vol and is_strong_trend:
            return "low_vol_trend"
        elif is_high_vol and not is_strong_trend:
            return "high_vol_range"
        elif not is_high_vol and not is_strong_trend:
            return "low_vol_range"
        else:
            return "unknown"


class AdaptiveParamsManager:
    """
    自适应参数管理器

    管理：
    1. 基于市场环境的参数切换
    2. 参数切换的平滑过渡（避免跳变）
    3. 切换历史记录
    """

    def __init__(self):
        self.current_regime: str = "unknown"
        self.current_params = StrategyParams()  # 默认参数
        self._regime_history: list[dict] = []
        self._last_switch_time: datetime | None = None

    def update_regime(self, features: pd.DataFrame) -> tuple[str, StrategyParams]:
        """更新市场环境，返回 (环境类型, 推荐参数)

        Args:
            features: build_feature_frame() 的输出

        Returns:
            (环境类型, 推荐参数组)
        """
        new_regime = RegimeDetector.detect_from_features(features)

        if new_regime != self.current_regime:
            old_regime = self.current_regime
            self.current_regime = new_regime

            # 获取新环境的参数
            new_params = REGIME_PARAMS.get(new_regime, StrategyParams())

            # 如果之前已有参数，做平滑过渡
            if old_regime != "unknown" and self._last_switch_time is not None:
                elapsed = (datetime.now(timezone.utc) - self._last_switch_time).total_seconds()
                if elapsed < 3600:  # 1小时内不重复切换
                    log.debug(f"Regime {old_regime}→{new_regime} but switched recently, keeping current params")
                    return self.current_regime, self.current_params

            self.current_params = new_params
            self._last_switch_time = datetime.now(timezone.utc)

            # 记录历史
            self._regime_history.append({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "old_regime": old_regime,
                "new_regime": new_regime,
                "params": asdict(new_params),
            })

            log.info(f"🌍 市场环境切换: {old_regime} → {new_regime} | 参数已更新")

        return self.current_regime, self.current_params

    def get_score_penalty(self) -> float:
        """获取当前环境的信号评分惩罚"""
        return REGIME_SCORE_PENALTY.get(self.current_regime, 0.0)

    def get_leverage_factor(self) -> float:
        """获取当前环境的杠杆调整系数"""
        return REGIME_LEVERAGE_FACTOR.get(self.current_regime, 1.0)

    def get_regime_name_cn(self) -> str:
        """获取当前环境的中文名"""
        names = {
            "high_vol_trend": "高波动趋势",
            "low_vol_trend": "低波动趋势",
            "high_vol_range": "高波动震荡",
            "low_vol_range": "低波动震荡",
            "unknown": "未知环境",
        }
        return names.get(self.current_regime, "未知")

    def get_regime_summary(self) -> dict:
        """获取环境摘要"""
        return {
            "current_regime": self.current_regime,
            "regime_name_cn": self.get_regime_name_cn(),
            "current_params": asdict(self.current_params),
            "score_penalty": self.get_score_penalty(),
            "leverage_factor": self.get_leverage_factor(),
            "switch_count": len(self._regime_history),
        }
