# OKX 合约信号系统 - 自进化信号研究平台

## 系统架构总览

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    Signal Research Brain (信号研究大脑)                  │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐    │
│  │ 在线学习    │  │ 强化学习    │  │ 币种轮换    │  │ 数据同步    │    │
│  │ Online      │  │ RL (Q-Learn)│  │ Symbol       │  │ Data Sync   │    │
│  │ Learning    │  │             │  │ Rotation     │  │             │    │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘    │
│         │                │                │                │            │
│         └────────────────┴────────────────┴────────────────┘            │
│                                   │                                      │
│                    ┌──────────────▼──────────────┐                       │
│                    │   信号参数 (StrategyParams)  │                       │
│                    │   自主优化，自适应市场变化   │                       │
│                    └──────────────┬──────────────┘                       │
└───────────────────────────────────┼─────────────────────────────────────┘
                                    │
                    ┌───────────────▼───────────────┐
                    │      OKX 行情接口              │
                    │  行情获取 / K线同步 / 合约资料 │
                    └───────────────┬───────────────┘
                                    │
                    ┌───────────────▼───────────────┐
                    │      飞书通知推送               │
                    │   信号卡片 / 状态报告 / 告警   │
                    └───────────────────────────────┘
```

## v3.47 SIGNAL_ONLY 运行边界

本系统是纯信号研究与飞书推送平台。正式发布链路只包含：

```text
OKX 行情数据 -> 信号研究 -> 信号质量复核 -> 飞书推送
```

### 回测结果保护

SIGNAL_ONLY 回测不依赖交易所下单数量。`validate_signal` 接受信号但不返回 `qty` 时，回测使用本地研究风险金额生成标准化结果行，并输出 `outcome`、`net_r` 和 `final_net_r`。`outcome` 统一收敛为质量模型支持的 `TP`、`SL` 和 `TIMEOUT` 三类。回测、质量标签和执行结果评估统一调用 `SignalOutcomeSimulator`，按下一根闭合 K 线开盘作为研究入场，并用同一套止损、目标和超时规则判定结果。参数搜索、每日学习、滚动回测、启动质量门和报告写入必须先校验回测结果；空结果或缺少核心列时只能失败或跳过，不能生成可信训练结论或正式报告。

### 正式信号主链

GUI、实时监控、调度器、报告任务和 TradingBrain 观察路径统一复用 `SignalScanService`。该服务负责闭合 K 线检查、最新 K 线边界、投票门、质量模型旁路、候选排序、A/B/C 分层和生命周期写入。只有 A 级正式候选会进入正式飞书信号推送和生命周期记录；B 级保留摘要，C 级只作为观察候选，不触发正式信号状态。A 级与 B 级飞书消息都会展示 `quality_model` 的旁路字段，便于人工复核。

Near-breakout C-tier observation now uses ATR distance (`distance_to_breakout / ATR <= 0.3`) instead of a fixed percent gap, and scan health / observation payloads carry the ATR distance for review.
### 生命周期持久化

正式生命周期状态从 JSON 文件升级为 SQLite，默认写入 `outputs/signal_lifecycle.sqlite3`。`lifecycle_records` 保存每条信号的当前状态，`lifecycle_events` 保存 `TRIGGERED`、`CONFIRMED`、`INVALIDATED`、`EXPIRED` 等状态变化流水，`notification_outbox` 保存正式 A 级飞书推送的待发送、已发送和失败结果。旧的 `outputs/signal_lifecycle.json` 在首次打开 SQLite store 时会迁移到新表，迁移后运行期不再依赖 JSON 文件。

### 数据根边界

历史数据根只读，用于读取已验证的本地历史 K 线；运行缓存根可写，用于保存系统启动后通过 OKX 公共行情补齐的新 K 线。实时缓存按策略预热需求保留至少 3500 根 15m K 线，避免慢趋势参数启动后因缓存过短而失效。

允许的产品行为：
- 读取 OKX 公开行情、K线与合约资料。
- 基于本地历史数据和配置币种生成研究信号。
- 记录信号状态，用于复盘、统计与报告。
- 发送飞书信号卡片、状态报告和告警。

发布边界：
- 不访问账户，不使用 OKX 私钥。
- 不提交、撤销或自动关闭订单。
- 不轮询真实持仓，不读取账户余额。
- 不提供可切换为自动化交易的配置示例。
- 止损、止盈、持有时长和风险值只作为研究标注，仅供人工复核。
- 本地历史数据通过 `JIAOYI_DATA_DIR` 或工作区发现载入，发布默认配置不绑定个人机器路径。
- 启动后的新增 K 线写入 `runtime_cache_root` 或默认 `outputs/runtime_cache`，不回写只读历史数据根。
- 正式飞书信号推送不包含 `qty`、`leverage`、`max_loss_pct` 或 `margin_loss_pct` 等交易执行语义。

所有用户可见时间统一显示为北京时间，包括 GUI 信号列表、Streamlit 面板和飞书推送；内部时间计算、历史数据存储和闭合 K 线对齐仍保留 UTC。

回测链路在 SIGNAL_ONLY 模式下不再要求真实交易数量。风控通过但未给出 `qty` 时，回测会按固定研究风险单位生成可比较的研究 sizing，并输出 `outcome`、`net_r` 和 `final_net_r`，用于训练、质量评估和报告闭环；这些字段不代表真实下单数量。

## 自进化能力详解

### 1. 在线学习 (Online Learning)

**原理**: 每次信号闭环后记录结果，根据表现调整参数

```python
# 核心机制
- 记录每条闭环信号: entry/exit价格, PnL, 胜率, 盈亏比
- 当累积 >= 20条闭环信号后评估表现
- 如果 PF < 1.0 或 WR < 35%，触发参数调整
- 调整幅度 = 5% (ADAPTATION_LEARNING_RATE)
```

**参数评分公式**:
```
Score = 0.6 × PF + 0.2 × WR + 0.2 × Return
```

### 2. 强化学习 (Q-Learning)

**原理**: 将市场环境离散化，学习不同环境下的最优信号参数

```python
# 状态空间
Market_Regime = {
    high_vol_trend,   # 高波动趋势
    low_vol_trend,    # 低波动趋势
    high_vol_range,   # 高波动震荡
    low_vol_range     # 低波动震荡
}

# 动作空间
- fast_ema: [10, 15, 20, 25, 30]
- slow_ema: [40, 50, 60, 70, 80]
- breakout_window: [20, 30, 40, 50, 60]
- atr_stop_mult: [1.5, 2.0, 2.5, 3.0]
- take_profit_mult: [1.5, 2.0, 3.0, 4.0, 5.0]
```

**Q-Learning 更新**:
```
Q(s,a) = Q(s,a) + α × [r + γ × max(Q(s',a')) - Q(s,a)]
```

### 3. 智能币种轮换

**原理**: 根据各币种信号表现动态调整关注列表

```python
# 评估指标
- Profit Factor (PF) >= 1.2
- Win Rate (WR) >= 35%
- 最少闭环信号数 >= 10

# 轮换规则
- 最多同时关注 5 个币种
- 每 24 小时重新评估
- 淘汰表现差的，引入表现好的
```

---

## 数据回补机制 (离线1-2天解决方案)

### 问题场景

```
系统离线1-2天后重新启动:

时间线: ─────────────────────────────────────────────────►
         │                    │                    │
      昨天00:00            昨天12:00          今天12:00(现在)
         │                    │                    │
         ▼                    ▼                    ▼
      最后数据           数据断裂点          系统重启
```

### 解决方案

```
┌─────────────────────────────────────────────────────────────────────┐
│                     启动时数据回补流程                               │
└─────────────────────────────────────────────────────────────────────┘

1. 检测本地数据末尾时间
          │
          ▼
2. 与OKX API实时数据对比
          │
          ├── 无缺口 → 直接使用本地数据
          │
          └── 有缺口 → 触发回补流程
                      │
                      ▼
3. 从OKX API分批获取缺失K线
   - 每批最多 300 根K线
   - 自动拆分为多个请求
          │
          ▼
4. 合并去重，更新本地数据文件
          │
          ▼
5. 标记不可靠的K线区域
   - 缺口后连续5根标记为 is_reliable=False
   - 特征计算时跳过或特殊处理
```

### 增量同步 (运行中)

```
每个扫描周期结束时:
│
▼ 检查各币种数据末尾时间
│
├── 距离上次同步 < 1小时 → 跳过
│
└── 距离上次同步 >= 1小时 → 同步最新K线
                                   │
                                   ▼
                              更新本地数据文件
```

### 代码示例

```python
# 启动时
from okx_signal_system.data import sync_on_startup

symbols = ["BTC-USDT-SWAP", "ETH-USDT-SWAP"]
results = sync_on_startup(symbols)

# 输出示例:
# BTC-USDT-SWAP: 48 bars added, 1 gaps filled [OK]
# ETH-USDT-SWAP: 0 bars added, 0 gaps filled [OK]

# 运行中
syncer = IncrementalSyncer()
syncer.sync_if_needed("BTC-USDT-SWAP", interval_hours=1)
```

### 数据可靠性标记

```python
# 检测NaN区域
nan_regions = FeatureGapHandler.detect_nan_regions(df)
# 返回: [(start_idx, end_idx), ...]

# 标记不可靠区域
df = FeatureGapHandler.mark_unreliable_bars(df, max_consecutive_nan=5)

# 生成信号时检查
if not df.loc[idx, "is_reliable"]:
    continue  # 跳过不可靠的K线
```

---

## 运行周期

```
15分钟扫描周期:
│
├── T+0:00  获取实时行情
├── T+0:01  生成研究信号
├── T+0:02  信号质量检查
├── T+0:03  推送飞书通知
├── T+0:04  同步最新K线数据
│
├── T+15:00 结束当前周期
│
└── 每2个周期(30分钟):
    └── 评估表现，自适应调整参数
        ├── 在线学习检查
        └── 强化学习优化

    每4个周期(1小时):
    └── 币种轮换评估
```

---

## 文件结构

```
src/okx_signal_system/
├── ml/
│   ├── online_learning.py    # 在线学习引擎
│   ├── reinforcement_learning.py  # Q-Learning强化学习
│   ├── symbol_rotation.py    # 币种轮换
│   └── trading_brain.py      # 信号研究主控
├── data/
│   ├── loader.py             # 数据加载
│   └── gap_handler.py        # 数据回补
├── exchange/
│   ├── okx_public.py         # OKX 公开行情只读适配器
│   ├── okx.py                # 只读适配器兼容导出
│   └── realtime.py           # 行情实时接口与信号扫描联动
└── notification/
    └── feishu.py              # 飞书推送
```

---

## 自进化学习循环

```
┌─────────────────────────────────────────────────────────────┐
│                      持续学习循环                            │
└─────────────────────────────────────────────────────────────┘

     ┌──────────────────┐
     │   市场数据输入    │
     └────────┬─────────┘
              │
              ▼
┌─────────────────────────────┐
│  1. 策略评估 → 产生信号      │
└─────────────┬───────────────┘
              │
              ▼
┌─────────────────────────────┐
│  2. 信号闭环 → 记录结果      │
└─────────────┬───────────────┘
              │
     ┌────────┴────────┐
     │                 │
     ▼                 ▼
┌─────────┐    ┌─────────────┐
│在线学习 │    │ 强化学习     │
│ 调整    │    │ Q-Table更新  │
│ 参数    │    │ 状态-动作映射│
└────┬────┘    └──────┬──────┘
     │                │
     └────────┬───────┘
              │
              ▼
┌─────────────────────────────┐
│  3. 更新策略参数            │
│     新参数 = f(历史表现)     │
└─────────────┬───────────────┘
              │
              ▼
┌─────────────────────────────┐
│  4. 币种轮换                │
│     根据PF/WR调整关注列表    │
└─────────────┬───────────────┘
              │
              └──────────────────► 下一轮
```

---

## 关键指标

| 指标 | 阈值 | 作用 |
|------|------|------|
| Profit Factor (PF) | >= 1.2 | 主要盈利指标 |
| Win Rate (WR) | >= 35% | 辅助指标 |
| 最少闭环信号数 | >= 10 | 统计显著性 |
| 最大同时币种 | 5 | 风险分散 |
| 评估周期 | 30分钟 | 快速响应 |
| 轮换周期 | 1小时 | 稳定观察 |

---

## 数据完整性保证

1. **启动时回补**: 系统启动时自动检测并填补所有数据缺口
2. **增量同步**: 每个扫描周期结束时同步最新K线
3. **可靠性标记**: 缺口附近的K线标记为不可靠
4. **容错处理**: 单个币种回补失败不影响其他币种
5. **状态持久化**: 所有学习成果保存到磁盘，重启后恢复

## v3.48 Acceptance Closure

- Lifecycle storage is SQLite-first: `lifecycle_records` stores current state, `lifecycle_events` stores every state transition, and `notification_outbox` stores pending, sent, and failed notification delivery state. Terminal research outcomes include `TARGET_REACHED`, `STOP_REACHED`, and `TIMEOUT_RESULT`.
- Feishu delivery is routed through `NotificationDispatcher` for A-tier signals, B-tier summaries, status reports, startup notifications, and candidate health reports. Legacy Feishu helpers remain as compatibility functions, but runtime entrypoints should use the dispatcher.
- User-facing signal times are Beijing time. Signal cards show signal generation time from the signal K-line timestamp when available, plus a separate notification send time. Runtime status JSON also exposes Beijing display fields alongside UTC machine timestamps.
- C-tier near-breakout observation is ATR-relative, and correlation grouping defaults to a 500-sample floor unless tests or research tools explicitly override it.
- Research sizing and slippage use shared helpers in `risk.costs`, so backtest and quality-label paths use the same risk unit and cost assumptions.
