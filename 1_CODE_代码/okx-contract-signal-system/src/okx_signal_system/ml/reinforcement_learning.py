"""
OKX 合约信号系统 - 强化学习自适应引擎
使用Q-Learning进行参数优化
"""
from __future__ import annotations

import json
import logging
import random
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import numpy as np

from okx_signal_system.strategy.trend_breakout import StrategyParams

log = logging.getLogger(__name__)

# Q-Learning 参数
LEARNING_RATE = 0.1  # alpha
DISCOUNT_FACTOR = 0.9  # gamma
EPSILON_START = 1.0  # 探索率初始值
EPSILON_MIN = 0.05  # 最小探索率
EPSILON_DECAY = 0.995  # 探索率衰减

# 参数离散化空间
FAST_EMA_VALUES = [96, 120, 144]
SLOW_EMA_VALUES = [576, 720, 960]
BREAKOUT_VALUES = [288, 384, 480]
ATR_STOP_VALUES = [4.0, 4.5]
TAKE_PROFIT_VALUES = [6.0, 7.0]
MAX_HOLD_VALUES = [576, 768]


@dataclass
class State:
    """状态空间：当前市场环境和策略表现"""
    market_regime: str  # high_vol_trend, low_vol_trend, high_vol_range, low_vol_range
    recent_pf: float  # 最近盈亏比 (0-3)
    recent_wr: float  # 最近胜率 (0-1)
    recent_return: float  # 最近收益率 (-1 to 1)


@dataclass
class Action:
    """动作空间：参数调整"""
    param_to_change: str
    new_value: float


@dataclass
class QEntry:
    """Q表条目"""
    q_value: float = 0.0
    visits: int = 0


class RLParameterOptimizer:
    """
    强化学习参数优化器
    使用Q-Learning自动优化策略参数
    """

    def __init__(self, data_dir: Path | str):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # Q表
        self.q_table: dict[str, dict[str, float]] = {}

        # 探索参数
        self.epsilon = EPSILON_START

        # 历史记录
        self.episodes: list[dict] = []

        # 状态历史
        self.state_history: list[State] = []

        self._load_q_table()

    def _state_key(self, state: State) -> str:
        """生成状态唯一键"""
        # 离散化状态
        pf_bin = int(state.recent_pf * 2)  # 0-6
        wr_bin = int(state.recent_wr * 4)  # 0-4
        ret_bin = int(state.recent_return * 2 + 2)  # 0-4
        return f"{state.market_regime}_{pf_bin}_{wr_bin}_{ret_bin}"

    def _action_key(self, action: Action) -> str:
        """生成动作唯一键"""
        return f"{action.param_to_change}_{action.new_value}"

    def _load_q_table(self):
        """加载Q表"""
        path = self.data_dir / "q_table.json"
        if path.exists():
            try:
                self.q_table = json.loads(path.read_text(encoding="utf-8"))
                log.info(f"Loaded Q-table with {len(self.q_table)} states")
            except Exception as e:
                log.warning(f"Failed to load Q-table: {e}")

    def _save_q_table(self):
        """保存Q表"""
        path = self.data_dir / "q_table.json"
        path.write_text(json.dumps(self.q_table, indent=2), encoding="utf-8")

    def _get_valid_actions(self) -> list[Action]:
        """获取所有有效动作"""
        actions = []

        # EMA调整
        for val in FAST_EMA_VALUES:
            actions.append(Action("fast_ema", val))
        for val in SLOW_EMA_VALUES:
            actions.append(Action("slow_ema", val))

        # 突破窗口
        for val in BREAKOUT_VALUES:
            actions.append(Action("breakout_window", val))

        # 止损止盈
        for val in ATR_STOP_VALUES:
            actions.append(Action("atr_stop_mult", val))
        for val in TAKE_PROFIT_VALUES:
            actions.append(Action("take_profit_mult", val))

        # 持仓时间
        for val in MAX_HOLD_VALUES:
            actions.append(Action("max_hold_bars", val))

        return actions

    def _choose_action(self, state: State) -> Action:
        """epsilon-greedy选择动作"""
        if random.random() < self.epsilon:
            # 探索：随机选择
            return random.choice(self._get_valid_actions())

        # 利用：选择Q值最高的动作
        state_key = self._state_key(state)
        actions = self._get_valid_actions()

        best_action = actions[0]
        best_q = -float("inf")

        for action in actions:
            action_key = self._action_key(action)
            q = self.q_table.get(state_key, {}).get(action_key, 0.0)
            if q > best_q:
                best_q = q
                best_action = action

        return best_action

    def _calculate_reward(self, metrics_before: dict, metrics_after: dict) -> float:
        """
        计算奖励
        基于性能改进
        """
        pf_before = metrics_before.get("profit_factor", 1.0)
        pf_after = metrics_after.get("profit_factor", 1.0)

        wr_before = metrics_before.get("win_rate", 0.0)
        wr_after = metrics_after.get("win_rate", 0.0)

        # 奖励 = PF改进 * 0.7 + WR改进 * 0.3
        reward = (pf_after - pf_before) * 2.0 + (wr_after - wr_before) * 0.5

        return reward

    def update(self, state: State, action: Action, reward: float, next_state: State):
        """更新Q表"""
        state_key = self._state_key(state)
        action_key = self._action_key(action)
        next_state_key = self._state_key(next_state)

        # 初始化
        if state_key not in self.q_table:
            self.q_table[state_key] = {}
        if action_key not in self.q_table[state_key]:
            self.q_table[state_key][action_key] = 0.0

        # 获取当前Q值和下一个状态的最大Q值
        current_q = self.q_table[state_key][action_key]
        next_max_q = max(
            self.q_table.get(next_state_key, {}).values() or [0.0]
        )

        # Q-Learning更新
        new_q = current_q + LEARNING_RATE * (reward + DISCOUNT_FACTOR * next_max_q - current_q)
        self.q_table[state_key][action_key] = new_q

        # 衰减探索率
        self.epsilon = max(EPSILON_MIN, self.epsilon * EPSILON_DECAY)

    def apply_action_to_params(self, params: StrategyParams, action: Action) -> StrategyParams:
        """将动作应用到参数"""
        params_dict = asdict(params)
        params_dict[action.param_to_change] = action.new_value
        params_dict["take_profit_mult"] = max(float(params_dict.get("take_profit_mult", 6.0)), 6.0)
        return StrategyParams(**params_dict)

    def get_best_action(self, state: State) -> Action | None:
        """获取当前状态下的最佳动作"""
        state_key = self._state_key(state)
        actions = self._get_valid_actions()

        best_action = None
        best_q = -float("inf")

        for action in actions:
            action_key = self._action_key(action)
            q = self.q_table.get(state_key, {}).get(action_key, 0.0)
            if q > best_q:
                best_q = q
                best_action = action

        return best_action

    def optimize_params(self, current_params: StrategyParams, state: State) -> StrategyParams:
        """优化参数"""
        action = self._choose_action(state)

        if action:
            new_params = self.apply_action_to_params(current_params, action)
            log.info(f"RL: Applied action {action.param_to_change}={action.new_value}, epsilon={self.epsilon:.3f}")
            return new_params

        return current_params

    def record_episode(self, state: State, action: Action, reward: float, next_state: State, new_params: StrategyParams):
        """记录一个学习回合"""
        episode = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "state": asdict(state),
            "action": asdict(action),
            "reward": reward,
            "next_state": asdict(next_state),
            "new_params": asdict(new_params),
            "epsilon": self.epsilon,
        }
        self.episodes.append(episode)

        # 保存
        self._save_episodes()
        self._save_q_table()

    def _save_episodes(self):
        """保存学习记录"""
        path = self.data_dir / "rl_episodes.json"
        path.write_text(json.dumps(self.episodes[-100:], indent=2, default=str), encoding="utf-8")

    def get_learning_stats(self) -> dict:
        """获取学习统计"""
        if not self.episodes:
            return {"episodes": 0, "epsilon": self.epsilon, "states_learned": 0}

        recent_rewards = [e["reward"] for e in self.episodes[-20:]]
        return {
            "episodes": len(self.episodes),
            "epsilon": f"{self.epsilon:.3f}",
            "states_learned": len(self.q_table),
            "avg_reward_20": f"{np.mean(recent_rewards):.3f}",
            "total_reward": f"{sum(recent_rewards):.3f}",
        }


class MarketRegimeDetector:
    """
    市场环境检测器
    识别当前市场状态用于RL状态空间
    """

    @staticmethod
    def detect_regime(
        atr_pct: float,
        atr_avg_ratio: float,
        ema_spread: float,
        volume_ratio: float,
    ) -> str:
        """
        检测市场环境
        返回: high_vol_trend, low_vol_trend, high_vol_range, low_vol_range
        """
        is_high_vol = atr_avg_ratio > 1.2
        is_strong_trend = abs(ema_spread) > 0.01

        if is_high_vol and is_strong_trend:
            return "high_vol_trend"
        elif not is_high_vol and is_strong_trend:
            return "low_vol_trend"
        elif is_high_vol and not is_strong_trend:
            return "high_vol_range"
        else:
            return "low_vol_range"

    @staticmethod
    def create_state_from_metrics(
        regime: str,
        recent_pf: float,
        recent_wr: float,
        recent_return: float,
    ) -> State:
        """从指标创建状态"""
        return State(
            market_regime=regime,
            recent_pf=min(recent_pf, 3.0),  # 上限3
            recent_wr=recent_wr,
            recent_return=max(-1.0, min(1.0, recent_return)),  # 限制在-1到1
        )


def create_rl_optimizer(data_dir: Path | str) -> RLParameterOptimizer:
    """创建RL优化器"""
    return RLParameterOptimizer(data_dir)
