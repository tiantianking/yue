# OKX 合约信号系统部署前后完整清单

适用版本：v3.56.28

本系统是 `SIGNAL_ONLY` 公共行情信号观察系统。部署过程不得配置 OKX 私有 API Key，不得加入下单、撤单、开仓、平仓或真实账户仓位逻辑。

## 一、部署前必须完成

### 1. 代码与发布基线

- [x] Python 全量单元测试通过。
- [x] Dashboard lint、TypeScript 类型检查、Node 测试和生产构建通过。
- [x] `git diff --check` 通过。
- [x] Python 生产模块完成语法编译。
- [x] 应用版本、approved strategy version、发布清单和文档分别登记且保持可审计。
- [x] 发布包排除 `.env`、SQLite、日志、运行缓存、`node_modules`、`.next` 和私密凭据。
- [ ] 在目标 VPS 上验证发布包 SHA-256 与发布记录一致。

### 2. 安全边界

目标环境文件必须明确包含：

```env
DEPLOYMENT_MODE=observation
SIGNAL_ONLY=true
DATA_READ_ONLY=true
OKX_IS_SIMULATED=true
OKX_AUTO_CLOSE_ENABLED=false
FEISHU_ENABLED=false
OKX_API_KEY=
OKX_SECRET_KEY=
OKX_PASSPHRASE=
```

检查项：

- [x] `SIGNAL_ONLY=false` 会阻止主程序启动。
- [x] `FEISHU_ENABLED` 环境变量是通知紧急总开关，优先于 YAML。
- [x] 私有 OKX 凭据非空会使生产预检失败。
- [x] `execution.live_order_enabled=false`。
- [x] `execution.auto_close_enabled=false`。
- [x] `execution.dry_run_enabled=true`。
- [x] 杠杆只作为人工参考建议，不能产生订单或仓位数量。

### 3. 正式策略批准

正式推送前必须存在：

```text
outputs/runtime/approved_strategy_manifest.json
```

该文件只能由合法 strict-research 产物晋级生成：

```bash
/opt/okx-signal/venv/bin/python -m okx_signal_system.research.promote --run-id <合法运行ID>
```

必须满足：

- [ ] 研究版本是 `v3.56-strict`。
- [ ] 应用版本是 `3.56.28`，approved strategy version 仍是 `3.56.15`。
- [ ] validation 与 blind 时间窗严格隔离。
- [ ] blind 状态为真实 `BLIND_SEALED_PASS`。
- [ ] 成本压力测试通过。
- [ ] 参数和币种覆盖完整。
- [ ] 没有未来函数。
- [ ] 样本外交易数量、PF、回撤、盈利币种比例和集中度全部过门槛。
- [ ] 操作者完成人工复核。

没有合法 manifest 时只能使用 `DEPLOYMENT_MODE=observation`。禁止手写、复制旧版本或降低门槛生成 manifest。

### 4. 飞书配置

- [ ] 在 VPS 私密环境文件中设置 `FEISHU_WEBHOOK_URL`。
- [ ] 观察阶段保持 `FEISHU_ENABLED=false`。
- [ ] 使用专用测试群验证启动通知和测试消息。
- [ ] 确认同一信号不会重复推送。
- [ ] 确认失败消息进入 outbox 重试而不是静默丢失。
- [ ] 正式启用前设置 `FEISHU_ENABLED=true`。

`.env` 权限必须为 `0600`，不得提交 Git。

### 5. VPS 网络

- [ ] VPS 可以直连 OKX REST 和 WebSocket；若可以，保持 `OKX_REST_PROXY` 与 `OKX_WS_PROXY` 为空。
- [ ] 若必须走代理，代理必须运行在 VPS 本机，不能复制 Windows 的 `127.0.0.1:1088` 配置后假定可用。
- [ ] 系统时间启用 NTP，同步到 UTC。
- [ ] 出站 DNS、HTTPS 443 和 OKX WebSocket 端口可用。
- [ ] 防火墙不开放无关入站端口；Dashboard 默认仅监听本机。

### 6. 持久化目录

必须持久保存：

```text
/opt/okx-signal/app/outputs/
/opt/okx-signal/app/logs/
/etc/okx-signal/okx-signal.env
```

包括：

- K线运行缓存；
- approved manifest；
- lifecycle/outbox SQLite；
- shadow ensemble SQLite；
- 最新扫描和补线状态；
- 日志。

升级不能删除 `outputs/` 和 `logs/`。安装脚本已按此规则保留目录。

### 7. 自动守护

仓库已提供：

```text
deployment/systemd/okx-signal.service
deployment/systemd/okx-signal-health.service
deployment/systemd/okx-signal-health.timer
deployment/logrotate/okx-signal
```

要求：

- [x] 主服务断开后自动重启。
- [x] 等待网络在线后启动。
- [x] 启动前执行部署预检。
- [x] 使用独立低权限用户 `okxsignal`。
- [x] 每五分钟执行健康检查。
- [x] 日志保留 14 天并压缩。
- [ ] 在目标 VPS 上确认开机自动启动。
- [ ] 实际重启 VPS 一次，确认服务自动恢复。

### 8. 自动预检

观察模式：

```bash
sudo -u okxsignal /opt/okx-signal/venv/bin/python \
  /opt/okx-signal/app/scripts/system_check.py preflight \
  --mode observation \
  --env-file /etc/okx-signal/okx-signal.env
```

正式模式：

```bash
sudo -u okxsignal /opt/okx-signal/venv/bin/python \
  /opt/okx-signal/app/scripts/system_check.py preflight \
  --mode production \
  --env-file /etc/okx-signal/okx-signal.env
```

正式模式只有在 manifest、飞书、目录权限和安全边界全部通过时才返回 0。

## 二、目标 VPS 安装流程

### 1. 上传并校验发布包

```bash
sha256sum okx-contract-signal-system-v3.56.24-final.zip
unzip okx-contract-signal-system-v3.56.24-final.zip -d /tmp/okx-signal-release
cd /tmp/okx-signal-release
```

### 2. 安装但不立即启动

```bash
sudo bash deployment/install_linux.sh
```

安装脚本会：

- 创建 `okxsignal` 系统用户；
- 安装到 `/opt/okx-signal/app`；
- 建立 Python venv；
- 安装锁定依赖；
- 安装 systemd、健康检查和 logrotate；
- 保留旧 `outputs/` 与 `logs/`；
- 执行观察模式预检；
- 默认不自动启动。

### 3. 配置环境

```bash
sudoedit /etc/okx-signal/okx-signal.env
sudo chmod 600 /etc/okx-signal/okx-signal.env
```

首次必须保持：

```env
DEPLOYMENT_MODE=observation
FEISHU_ENABLED=false
```

### 4. 启动观察模式

```bash
sudo systemctl start okx-signal.service
sudo systemctl start okx-signal-health.timer
sudo systemctl status okx-signal.service
sudo journalctl -u okx-signal.service -f
```

### 5. 观察模式验收

至少验证：

- [ ] 21 个币种全部订阅。
- [ ] WebSocket connected 且没有持续重连。
- [ ] 15m 闭合 K 线补齐。
- [ ] 5m Dashboard 缓存补齐。
- [ ] `latest_scan_status.json` 持续刷新。
- [ ] 服务重启后 SQLite 和缓存可恢复。
- [ ] 健康定时器连续通过。
- [ ] CPU、内存、磁盘稳定。
- [ ] 无飞书消息，因为总开关关闭。

运行健康检查：

```bash
sudo -u okxsignal /opt/okx-signal/venv/bin/python \
  /opt/okx-signal/app/scripts/system_check.py runtime --mode observation
```

### 6. 切换正式推送

只有合法 manifest 已生成并通过生产预检后：

```env
DEPLOYMENT_MODE=production
FEISHU_ENABLED=true
FEISHU_WEBHOOK_URL=<私密Webhook>
```

然后：

```bash
sudo systemctl restart okx-signal.service
sudo -u okxsignal /opt/okx-signal/venv/bin/python \
  /opt/okx-signal/app/scripts/system_check.py preflight \
  --mode production --env-file /etc/okx-signal/okx-signal.env
sudo -u okxsignal /opt/okx-signal/venv/bin/python \
  /opt/okx-signal/app/scripts/system_check.py runtime --mode production
```

## 三、部署后必须做什么

### 上线当天

- 检查服务、健康 timer、WebSocket、closed backfill 和 outbox。
- 检查一次启动通知是否发送且只有一条。
- 检查 Dashboard 与状态文件显示的 push permission 一致。
- 人工核对第一条 A 级信号的入场、止损、目标、杠杆建议和失效时间。
- 确认 B 级摘要不会被误认为正式信号。
- 保存发布版本、Git commit、ZIP SHA-256 和 manifest SHA-256。

### 每日

- 检查 `systemctl status okx-signal.service`。
- 检查 `okx-signal-health.timer` 最近结果。
- 检查 WebSocket reconnect count 是否异常增长。
- 检查 21 个币种是否存在 stale 或缺口。
- 检查 outbox pending、failed 和 dead letter。
- 检查磁盘剩余空间。
- 复核当天推送是否重复或漏发。

### 每周

- 备份 lifecycle/outbox SQLite、approved manifest 和配置文件。
- 统计 A级数量、确认率、失效率、MFE、MAE、净R和按币种分布。
- 检查信号是否过度集中于单币种或单方向。
- 检查实际滑点和手续费假设是否仍合理。
- 检查日志错误、重连和健康失败记录。
- 验证备份可恢复。

### 每月或每累计足够样本后

- 对前向信号进行样本外评估。
- 比较正式策略与影子策略，但不得自动晋级。
- 重新校准质量模型并验证概率校准误差。
- 检查策略衰减、市场状态漂移和相关性集中。
- 只有通过完整 strict-research 才能升级正式参数。

## 四、告警与停推条件

出现任一情况，应立即设置 `FEISHU_ENABLED=false` 或切回 observation：

- manifest 无效或版本不一致；
- 数据缺口或最新闭合 K 线过期；
- WebSocket 持续断线；
- outbox 出现 dead letter；
- 同一信号重复发送；
- 信号使用未闭合 K 线；
- 风险保护字段缺失；
- 质量模型字段异常；
- 系统时间漂移；
- 发布文件校验不一致。

紧急停推不需要停止行情采集：

```bash
sudoedit /etc/okx-signal/okx-signal.env
# FEISHU_ENABLED=false
sudo systemctl restart okx-signal.service
```

## 五、升级和回滚

升级前：

- 备份 `outputs/`、`logs/` 和环境文件；
- 记录当前 commit、版本和 manifest hash；
- 在 observation 模式验证新版本；
- 新版本变更 approved strategy version 时，旧 manifest 必须 fail-closed。

回滚时：

- 恢复上一份经过校验的发布包；
- 不恢复与版本不匹配的 manifest；
- 先以 observation 模式启动；
- 健康检查通过后再决定是否恢复正式推送。
