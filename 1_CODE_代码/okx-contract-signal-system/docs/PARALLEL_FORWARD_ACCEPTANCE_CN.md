# 并行前向验收与提前淘汰

## 目标

系统同时运行三条互不混淆的工作线：

1. 已冻结的14日动量与“4入6出”继续记录真实前向信号，并只以“研究级/影子信号（非A级）”推送。
2. 新机制继续通过统一研究门禁；门禁全部通过后，登记为独立前向轨道，不等待现有轨道满90天。
3. 所有轨道按预先冻结的14/30/45/60/90日检查点运行，严重失败可提前永久归档。

本模块不下单、不持仓、不自动调参、不自动晋级A级。

## 固定检查点

| 检查点 | 作用 | 是否可以因一般短期亏损淘汰 |
| --- | --- | --- |
| 14天 | 只检查数据、信号、账本和快照链健康 | 否 |
| 30天 | 检查样本捕获是否严重不足；只有基础与压力成本结果同时灾难性失败才淘汰 | 否，必须满足联合严重失败条件 |
| 45天 | 检查单币和少数交易是否同时极端集中 | 否，必须满足联合极端集中条件 |
| 60天且至少50次完整换仓 | 执行既有固定正式门槛，允许形成“前向存活候选” | 是 |
| 90天且至少50次完整换仓 | 优先最终确认；通过后也只进入人工晋级复核 | 是 |

阈值冻结在 `config/parallel_acceptance_early_stop_protocol.json`，并由程序固定校验 SHA-256；`parallel_acceptance.yaml` 不能改写这些阈值。冻结时已存在1个闭合观察、2次前向换仓，已在协议中如实记录；提前淘汰不追溯执行，且这些阈值不得再根据后续收益修改。

## 当前轨道

`momentum_14d_and_4in6out` 已作为既有冻结参考轨道登记，包含：

- `original`：原始14日动量；
- `hysteresis_4_in_6_out`：固定4入6出。

飞书消息明确包含：`RESEARCH_ONLY / NOT_A_TIER / SIGNAL_ONLY`。

## 新候选如何进入并行前向

新候选不能直接写入配置。必须先运行统一研究门禁：

```bat
..\..\LOCAL_DEPS\venv\Scripts\python.exe scripts\system_check.py research ^
  --candidate config\research_candidates\NEW_CANDIDATE.json ^
  --artifacts outputs\research\NEW_CANDIDATE
```

只有生成的 `research_gate_report.json` 满足：

- `schema = okx_research_gate_report_v2`；
- `ok = true`；

才可在 `config/parallel_acceptance.yaml` 的 `tracks` 中登记。每条轨道必须提供：

- 唯一 `track_id`；
- 独立的前向状态文件；
- 独立的前向账本；
- 自己的更新脚本；
- 已通过的研究门禁报告；
- 冻结的变体名称与显示标签。

示例：

```yaml
- track_id: new_candidate_x
  label: 新候选X
  source_status: path/to/FORWARD_ACCEPTANCE_STATUS.json
  source_ledger: path/to/FORWARD_SHADOW_LEDGER.json
  updater_script: path/to/forward_shadow_updater.py
  admission_report: outputs/research/NEW_CANDIDATE/research_gate_report.json
  variants:
    frozen: 新候选X冻结版
```

`admission_exempt_frozen_reference: true` 只允许用于本次上线前已经冻结并正在取证的参考轨道，禁止给新候选绕过门禁。

## 运行

双击项目根目录：

```text
RUN_PARALLEL_ACCEPTANCE.cmd
```

它会：

1. 使用 `D:\JIAOYI-CX\LOCAL_DEPS\venv`；
2. 批量运行候选工厂并写入 `outputs/candidate_factory_status.json`；
3. 更新每条已登记轨道的前向证据；
4. 校验冻结的提前淘汰协议并执行并行验收状态机；
5. 对新信号发送研究级飞书摘要；
6. 写入 `outputs/parallel_acceptance_status.json`；
7. 将触发冻结失败规则的轨道永久归档。

日志位于 `logs/parallel_acceptance.log`。

## 失败归档

优先使用桌面已有的“失败策略”“失败策略文件夹”或 `Failed Strategies` 目录；若均不存在，则使用 `outputs/failed_research`。也可通过环境变量 `FAILED_RESEARCH_ARCHIVE_DIR` 指定。

归档包含：

- 前向状态；
- 前向账本；
- 研究门禁报告（若有）；
- 失败原因和证据哈希；
- `禁止调参营救.txt`。

同一证据只归档一次。失败候选不得改名、事后调参或删样本后重新申请同一机制。
