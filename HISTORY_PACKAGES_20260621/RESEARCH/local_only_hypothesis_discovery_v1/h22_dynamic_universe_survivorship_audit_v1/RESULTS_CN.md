# H22动态交易宇宙存活者偏差审计

状态：`COMPLETE`

决定：`SURVIVORSHIP_DEPENDENT_HISTORICAL_SUPPORT_REJECTED_NO_RESCUE`

协议：`H22_DYNAMIC_UNIVERSE_SURVIVORSHIP_AUDIT_V1`

## 一、研究边界

本轮只替换H22的币种名单，不修改14日形成期、4入6出、三组错开、3日刷新、04:00 UTC入场、0.4总敞口和成本门槛。动态名单在每个信号时点只使用当时已经闭合的数据：连续85根4小时K线，并按过去84根成交额选择前18名。

## 二、数据与宇宙

- 数据集质量：`PASS`；
- 历史标的总数：307；
- 动态宇宙实际使用过的标的：174；
- 每个时点进入成交额排名前的合格标的：最少 92，中位数 193.0，最多 216；
- 动态18币与固定18币平均重合：7.59 个；
- 最低重合：4 个，最高重合：11 个；
- 平均Jaccard重合度：0.2707；
- 动态名单发生变化的信号日：550。

## 三、固定18币同源基准

| 成本 | PF | 胜率 | 盈亏比 | 总收益 | 最大回撤 | 平均换手 |
|---|---:|---:|---:|---:|---:|---:|
| 基础成本 | 1.0970 | 0.5044 | 1.0777 | 0.1581 | -0.1000 | 0.0756 |
| 压力成本 | 0.9863 | 0.4848 | 1.0484 | -0.0348 | -0.1687 | 0.0756 |

## 四、动态点时18币结果

| 成本 | PF | 胜率 | 盈亏比 | 总收益 | 最大回撤 | 平均换手 |
|---|---:|---:|---:|---:|---:|---:|
| 基础成本 | 0.9247 | 0.4838 | 0.9867 | -0.2674 | -0.3893 | 0.0981 |
| 压力成本 | 0.8730 | 0.4720 | 0.9767 | -0.3975 | -0.4771 | 0.0981 |

动态基础成本正收益分段：0 / 3。

动态压力成本正收益分段：0 / 3。

最大单币正贡献占比：0.1136（SUI-USDT-SWAP）。

最大单月正贡献占比：0.1628（2025-01）。

决策时点强制退出次数：851。

持有期内退市终止退出次数：2。

## 五、固定门禁

- 失败：`dynamic_base_profit_factor`
- 失败：`dynamic_stress_profit_factor`
- 失败：`dynamic_base_total_return_gt_zero`
- 失败：`dynamic_stress_total_return_gt_zero`
- 失败：`dynamic_base_maximum_drawdown`
- 失败：`dynamic_stress_maximum_drawdown`
- 失败：`dynamic_positive_base_segments`
- 失败：`dynamic_positive_stress_segments`
- 通过：`maximum_single_symbol_positive_net_contribution_share`
- 通过：`maximum_single_month_positive_net_contribution_share`
- 通过：`minimum_mean_fixed_panel_overlap_count`
- 失败：`maximum_fixed_symbol_overlapping_open_relative_difference`
- 通过：`maximum_fixed_symbol_overlapping_close_relative_difference`


失败门禁数量：9。

失败门禁：dynamic_base_profit_factor, dynamic_stress_profit_factor, dynamic_base_total_return_gt_zero, dynamic_stress_total_return_gt_zero, dynamic_base_maximum_drawdown, dynamic_stress_maximum_drawdown, dynamic_positive_base_segments, dynamic_positive_stress_segments, maximum_fixed_symbol_overlapping_open_relative_difference。

## 六、结论约束

`SURVIVORSHIP_DEPENDENT_HISTORICAL_SUPPORT_REJECTED_NO_RESCUE`

通过也只代表H22的历史证据不完全依赖今天仍存活的固定币种名单，仍必须继续前向影子验收；失败则不得通过改成其他成交额窗口、其他动态币种数量、删除退市币或挑选月份来补救。本轮不会改变正式信号、A级状态、杠杆或下单边界。
