# 项目改动、归档与 GitHub 同步制度

## 一、适用范围

以下任一内容变化都属于正式项目改动：

- Python、TypeScript、PowerShell、批处理或部署脚本；
- 系统配置、风险参数、策略规则、研究门禁；
- 面板、飞书推送、信号生命周期和数据处理逻辑；
- 发布文件清单、依赖和运行入口。

纯粹补充历史说明且不改变代码行为的文档修订可以不提升应用版本，但仍需提交并同步 GitHub。

### 本地研究与生产发布隔离制度

- `RELEASE_FILES.txt` 是生产运行白名单，不是完整仓库文件清单；
- 新研究门禁、候选模板、回测器、参数搜索、研究协议、试验账本、失败归档工具和研究测试默认只留在本地仓库，禁止自动加入生产白名单；
- 只有被 `main.py`、`gui.py`、Dashboard、`scripts/runtime_check.py` 或其运行依赖实际调用的模块，才允许进入生产发布清单；
- 生产运行不得导入 `scripts/system_check.py`、`okx_signal_system.backtest`、`okx_signal_system.research` 或 `okx_signal_system.training`；
- 拆分研究工具时不得删除闭合K线、时效、21币覆盖、固定参数哈希、风险、质量分级、相关性、去重、过期清理、飞书outbox和禁止下单等运行信号门禁；
- V357等隔离前向观察文件只有在桌面运行链存在真实依赖时才能保留，必须继续与正式信号生命周期隔离。

## 二、代码改动的强制完成条件

代码改动只有同时满足以下条件才算完成：

1. `pyproject.toml`、Python 包版本和包元数据版本一致；
2. `docs/PROJECT_OVERVIEW_CN.md` 的当前版本和受影响内容已更新；
3. 存在当前版本的 `docs/V<版本>_RELEASE_CN.md`；
4. `RELEASE_FILES.txt` 和发行源清单已更新；
5. 完整 pytest 测试通过；
6. `scripts/system_check.py source --json` 通过；
7. 发布包构建成功；
8. 本地 Git 已提交；
9. 提交已推送到 `origin/master`；
10. 本地 `HEAD` 与跟踪分支一致，不得处于 `ahead` 状态。

任何一步未完成，都必须明确标记当前状态，禁止使用“已经全部完成并同步”的表述。

## 三、文档同步规则

每次代码改动至少同步更新两处：

- `docs/PROJECT_OVERVIEW_CN.md`：更新当前版本、系统现状或受影响模块；
- 当前版本发布说明：说明改动、验证结果、安全边界和兼容影响。

涉及研究流程时还要更新相应研究协议；涉及部署时更新部署清单；涉及运行状态时更新运行验证文档。

## 四、失败策略归档规则

默认永久归档位置为桌面：

`C:\Users\26492\Desktop\失败策略`

失败候选必须在确认失败的同一轮工作中完成归档，至少包括：

- 候选身份和冻结规则；
- 失败阶段；
- 失败门禁与关键数值；
- 是否打开过收益；
- 禁止营救项；
- 中文失败说明；
- 可用的原始报告和工件。

程序自动归档时应生成：

- `failure_summary.json`；
- `失败说明.md`；
- 候选 JSON；
- `research_gate_report.json`；
- `robustness_screen.json`；
- 存在时复制成本、交易和组合结果。

失败候选不允许更名后重新进入候选池，也不允许通过修改参数、删除币种、改变方向或降低成本重新测试。

## 五、GitHub 同步规则

默认远端和分支：

- 远端：`origin`
- 分支：`master`

远端同步必须在具备 GitHub 写入权限的授权环境中完成。

同步后必须运行：

```powershell
CHECK_REMOTE_SYNC.cmd
```

该入口确认本地工作区干净，并验证本地 `HEAD` 与 `origin/master` 一致。

若网络、凭证或工具权限阻止推送，应保留本地提交并明确报告：

- 本地提交哈希；
- 当前分支领先上游的提交数；
- GitHub 尚未同步；
- 需要执行的唯一同步入口。

不得绕过安全限制读取、修改或输出 GitHub 凭证。

## 六、发布检查命令

```powershell
py -3.12 -m pytest -q
py -3.12 scripts\system_check.py source --json
py -3.12 scripts\check_change_governance.py
py -3.12 scripts\build_release_zip.py
```

推送后最终检查：

```powershell
py -3.12 scripts\check_change_governance.py --require-github-sync
```

## 七、职责边界

- 代码改动必须测试、记录、提交和同步；
- 失败策略必须归档到桌面；
- 研究影子不能因代码发布自动晋升；
- GitHub 同步不能替代策略验证；
- 任何远端未同步状态都必须如实说明。
