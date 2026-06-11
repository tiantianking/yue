# 参数冻结说明

- 训练网格行数：1296
- 选择规则：先过硬阈值，再按盈亏比、PF、回撤、交易数、参数居中排序。
- 验证规则：冻结后只运行一次验证段，不用验证结果回改参数。
- 冻结参数：`{"fast_ema": 10, "slow_ema": 80, "breakout_window": 60, "atr_stop_mult": 1.5, "take_profit_mult": 2.0, "max_hold_bars": 24, "atr_window": 14}`