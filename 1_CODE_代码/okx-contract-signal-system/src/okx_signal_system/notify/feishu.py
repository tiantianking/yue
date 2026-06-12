"""
OKX合约信号系统 - 飞书通知模块（精简版）

只推送两种通知：
1. 交易信号告警（风控通过 + 综合评分≥6 的高质量信号）
2. 止盈止损平仓通知（含盈亏明细）
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

import requests

log = logging.getLogger(__name__)

FEISHU_WEBHOOK_URL = "https://open.feishu.cn/open-apis/bot/v2/hook/a5fb1dc8-91a4-483f-afe0-218118a8ab28"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def send_text(text: str, webhook_url: str = FEISHU_WEBHOOK_URL, max_retries: int = 3) -> bool:
    """发送纯文本消息到飞书（带重试机制）"""
    payload = {
        "msg_type": "text",
        "content": {"text": text},
    }
    for attempt in range(max_retries):
        try:
            resp = requests.post(webhook_url, json=payload, timeout=10)
            if resp.status_code == 200:
                log.info("飞书推送成功")
                return True
            else:
                log.warning(f"飞书推送失败(尝试 {attempt+1}/{max_retries}): {resp.status_code} {resp.text}")
        except Exception as e:
            log.error(f"飞书推送异常(尝试 {attempt+1}/{max_retries}): {e}")
        if attempt < max_retries - 1:
            import time
            time.sleep(2 ** attempt)
    log.error(f"飞书推送最终失败，已重试 {max_retries} 次")
    return False


def send_signal_alert(
    inst_id: str,
    side: str,
    entry_ref: float,
    stop_loss: float,
    take_profit: float,
    qty: float,
    leverage: float,
    reason: str = "",
    *,
    signal_score: float | None = None,
    risk_reward_ratio: float | None = None,
    stop_reason: str = "",
    tp_reason: str = "",
    max_loss_pct: float | None = None,
    kline_time: str | None = None,
) -> bool:
    """发送交易信号告警到飞书（仅高质量信号）"""
    direction_emoji = "🟢" if side == "long" else "🔴"
    direction_text = "做多" if side == "long" else "做空"
    ts = _now_utc().strftime("%Y-%m-%d %H:%M:%S")

    # 如果提供了K线时间且与检测时间差异大，同时显示两个时间
    time_line = f"📅 检测时间: {ts} UTC\n"
    if kline_time and kline_time != ts[:16].replace("-", "").replace(" ", "").replace(":", "")[:10]:
        time_line += f"📊 K线时间: {kline_time}\n"

    # 计算关键指标
    stop_dist_pct = abs(entry_ref - stop_loss) / entry_ref * 100
    tp_dist_pct = abs(take_profit - entry_ref) / entry_ref * 100

    if risk_reward_ratio is None:
        risk_reward_ratio = tp_dist_pct / stop_dist_pct if stop_dist_pct > 0 else 0

    # 信号强度评级
    if signal_score is not None:
        if signal_score >= 9:
            strength_text = "🟢 极强"
        elif signal_score >= 7:
            strength_text = "🟢 强"
        elif signal_score >= 6:
            strength_text = "🟡 中等偏强"
        else:
            strength_text = "🟠 中等"
        strength_line = f"📊 信号强度: {strength_text} ({signal_score:.1f}/10)\n"
    else:
        strength_line = ""

    # 止盈止损说明
    if not stop_reason:
        stop_reason = f"ATR × 止损倍数 = 入场价 ± {stop_dist_pct:.2f}%"
    if not tp_reason:
        tp_reason = f"止损距离 × 盈亏比 ({risk_reward_ratio:.1f}:1) = 入场价 ± {tp_dist_pct:.2f}%"

    # 最大亏损说明
    if max_loss_pct is not None:
        loss_line = f"⚠️ 最大单笔亏损: 本笔仓位 {max_loss_pct:.0%}\n"
    else:
        loss_line = ""

    text = (
        f"{direction_emoji} **交易信号**\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"{time_line}"
        f"📋 合约: {inst_id}\n"
        f"📈 方向: {direction_text}\n"
        f"💰 入场价: {entry_ref:.4f}\n"
        f"🛑 止损价: {stop_loss:.4f}\n"
        f"🎯 止盈价: {take_profit:.4f}\n"
        f"📊 数量: {qty:.4f}\n"
        f"⚙️ 杠杆: {leverage:.1f}x\n"
        f"📐 盈亏比: {risk_reward_ratio:.1f}:1\n"
        f"{strength_line}"
        f"{loss_line}"
        f"━━━━━━━━━━━━━━━━\n"
        f"📝 **止盈止损合理性说明**\n"
        f"🛑 止损逻辑: {stop_reason}\n"
        f"🎯 止盈逻辑: {tp_reason}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"{f'📝 备注: {reason}' if reason else ''}\n"
        f"_请人工确认后执行_"
    )
    return send_text(text)


def send_close_notification(
    inst_id: str,
    side: str,
    entry_price: float,
    exit_price: float,
    size: float,
    exit_reason: str,
    gross_pnl: float,
    net_pnl: float,
    net_pnl_pct: float,
    entry_fee: float = 0,
    exit_fee: float = 0,
    slippage_cost: float = 0,
    funding_fee: float = 0,
    total_costs: float = 0,
    signal_score: float | None = None,
) -> bool:
    """发送止盈止损平仓通知到飞书（含盈亏明细）"""
    reason_emoji = "🛑" if exit_reason == "stop_loss" else "🎯"
    reason_text = "止损" if exit_reason == "stop_loss" else "止盈"
    side_text = "多" if side == "long" else "空"
    pnl_emoji = "📈" if net_pnl >= 0 else "📉"

    ts = _now_utc().strftime("%Y-%m-%d %H:%M:%S")

    text = (
        f"{reason_emoji} **自动{reason_text}平仓**\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📅 时间: {ts} UTC\n"
        f"📋 合约: {inst_id}\n"
        f"📈 方向: {side_text}头\n"
        f"💰 开仓价: {entry_price:.4f}\n"
        f"💹 平仓价: {exit_price:.4f}\n"
        f"📊 数量: {size:.4f}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"{pnl_emoji} **盈亏明细**\n"
        f"💵 毛盈亏: {gross_pnl:+.4f} USDT\n"
        f"📝 开仓手续费: -{entry_fee:.4f}\n"
        f"📝 平仓手续费: -{exit_fee:.4f}\n"
        f"📝 滑点成本: -{slippage_cost:.4f}\n"
        f"📝 资金费率: -{funding_fee:.4f}\n"
        f"📝 总费用: -{total_costs:.4f}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"💰 **净盈亏: {net_pnl:+.4f} USDT ({net_pnl_pct:+.2%})**\n"
    )

    if signal_score is not None:
        text += f"📊 信号强度: {signal_score:.1f}/10\n"

    text += f"━━━━━━━━━━━━━━━━"

    return send_text(text)


# ============================================================
# 兼容旧 API 的别名
# ============================================================
def feishu_send_text(text: str, **kwargs) -> bool:
    """兼容旧 API：发送纯文本"""
    return send_text(text)

def feishu_send_signal_card(
    inst_id: str,
    direction: str,
    qty: float,
    leverage: float,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    reason: str,
) -> bool:
    """兼容旧 API：发送交易信号卡片"""
    side = "long" if direction == "long" else "short"
    return send_signal_alert(inst_id, side, entry_price, stop_loss, take_profit, qty, leverage, reason)

def feishu_test() -> bool:
    """兼容旧 API：测试飞书连接"""
    return send_text("🧪 OKX交易系统连接测试成功！")

# 兼容旧 API 的常量别名
FEISHU_WEBHOOK = FEISHU_WEBHOOK_URL
