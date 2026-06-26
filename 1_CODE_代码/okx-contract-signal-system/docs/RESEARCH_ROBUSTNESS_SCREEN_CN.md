# 新策略稳健性筛选协议

本协议只用于研究候选，不改变实时信号、前向影子、A级判定或任何订单路径。候选未提交完整证据时一律失败关闭，不允许用补参数、改币种或缩短样本营救。

## 一、执行顺序

1. 收益前机制、付款者、唯一方向、数据语义和失败家族去重。
2. 冻结参数空间、成本、成交时点、样本切分和本稳健性协议。
3. 仅在校准段生成三类标准证据。
4. 通过后才允许进入锁定验证；失败立即归档。
5. 锁定验证通过后才可进入独立前向影子，不自动晋升A级。

## 二、冻结协议

候选JSON必须包含：

```json
{
  "robustness_protocol": {
    "schema": "okx_robustness_screen_protocol_v1",
    "random_time_trials": 500,
    "random_time_alpha": 0.05,
    "entry_delay_bars": 1,
    "minimum_neighbor_variants": 3,
    "minimum_positive_neighbor_ratio": 0.6666666666666666,
    "portfolio_increment_required": true,
    "locked_before_pnl": true
  }
}
```

任何弱化、缺失或收益打开后补写均不被接受。

## 三、标准证据文件

### 1. `falsification_trials.csv`

必需列：

- `test`
- `trial_id`
- `net_r`
- `profit_factor`
- `total_trades`

必须包含且仅包含一行 `observed`、一行 `direction_reversed`、一行 `entry_delay_1bar`，并至少包含500行 `random_time`。

通过条件：

- 真实候选净R高于随机时间样本的95%分位；
- 加一平滑后的经验p值不高于0.05；
- 反向版本PF至少比真实版本低0.10，且反向净R不超过真实净R的25%；
- 延迟一根K线后PF不低于1、净R仍为正、净R保留率不低于35%。

随机试验必须保持原策略的信号数量、方向数量、持有期和可交易时间约束，不得把不可成交时点加入随机池。

### 2. `parameter_neighborhood.csv`

必需列：

- `config_id`
- `is_primary`
- `distance`
- `net_r`
- `profit_factor`
- `total_trades`

必须有且仅有一个主参数行，并至少有三个在收益打开前声明的相邻参数行。`distance`为标准化参数距离，直接邻域使用`0 < distance <= 1`。

通过条件：

- 至少三分之二邻域净R为正；
- 邻域PF中位数不低于1；
- 主参数PF不得超过邻域PF中位数的2倍。

此检查寻找连续盈利平台，而不是最佳参数。旧策略若冻结协议禁止补测邻域，则状态保持未知，不得追溯调参。

### 3. `portfolio_increment.csv`

必需列：

- `scenario`
- `profit_factor`
- `max_drawdown`
- `max_loss_streak`
- `effective_signal_count`
- `regime_coverage_count`

必须包含一行 `baseline` 和一行 `combined`。baseline为当前已冻结影子组合，combined为加入新候选后的同一时间、同一成本和同一风险预算结果。

组合不得出现明显恶化：

- PF下降不得超过0.03；
- 最大回撤增加不得超过0.02；
- 最大连续亏损增加不得超过1笔。

同时至少改善一项：

- PF提高至少0.03；
- 最大回撤降低至少0.02；
- 最大连续亏损减少至少2笔；
- 有效信号增加至少10%，且不少于5个；
- 覆盖的预声明市场状态至少增加1类。

## 四、系统输出

`system_check.py research`会在候选工件目录生成`robustness_screen.json`。

- 全部通过：`PASS_TO_LOCKED_VALIDATION`
- 任一失败或证据缺失：`FAIL_STOP_NO_RESCUE`

该结果是阻断门禁。它不能自动修改生产策略、自动推送A级信号或自动下单。

## 五、已有策略处理

已在独立前向观察中的14日动量与4入6出版本继续遵守原冻结协议：

- 已完成的执行延迟测试可作为成交时点稳健性证据；
- 原协议禁止事后补测参数邻域，因此参数平台状态保持未知；
- 不因新门禁追溯停用，也不因此视为已通过新门禁；
- 日频版本最终仍由60/90天和至少50次闭合前向观察的原冻结验收协议判定；固定3日低换手版按独立样本档案在90天/30次做阶段存活判断、150天/50次做最终人工复核。
