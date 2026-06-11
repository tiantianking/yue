from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

import requests

log = logging.getLogger(__name__)

FEISHU_WEBHOOK_URL = "https://open.feishu.cn/open-apis/bot/v2/hook/a5fb1dc8-91a4-483f-afe0-218118a8ab28"


@dataclass
class FeishuMessage:
    """飞书消息体"""

    msg_type: Literal["text", "post"] = "text"
    content: dict | None = None

    def to_dict(self) -> dict:
        return {"msg_type": self.msg_type, "content": self.content}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def send_text(text: str, webhook_url: str = FEISHU_WEBHOOK_URL) -> bool:
    """发送纯文本消息到飞书"""
    payload = {
        "msg_type": "text",
        "content": {"text": text},
    }
    try:
        resp = requests.post(webhook_url, json=payload, timeout=10)
        if resp.status_code == 200:
            log.info("飞书推送成功")
            return True
        else:
            log.warning(f"飞书推送失败: {resp.status_code} {resp.text}")
            return False
    except Exception as e:
        log.error(f"飞书推送异常: {e}")
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
) -> bool:
    """发送交易信号告警到飞书"""
    direction_emoji = "🟢" if side == "long" else "🔴"
    direction_text = "做多" if side == "long" else "做空"
    ts = _now_utc().strftime("%Y-%m-%d %H:%M:%S")

    text = (
        f"{direction_emoji} **交易信号**\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📅 时间: {ts} UTC\n"
        f"📋 合约: {inst_id}\n"
        f"📈 方向: {direction_text}\n"
        f"💰 入场价: {entry_ref:.4f}\n"
        f"🛑 止损价: {stop_loss:.4f}\n"
        f"🎯 止盈价: {take_profit:.4f}\n"
        f"📊 数量: {qty:.4f}\n"
        f"⚙️ 杠杆: {leverage:.1f}x\n"
        f"{f'📝 备注: {reason}' if reason else ''}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"_请人工确认后执行_"
    )
    return send_text(text)


def send_status_report(
    cycle_count: int,
    equity: float,
    open_positions: int,
    status: str,
    loss_streak: int,
    max_drawdown: float,
) -> bool:
    """发送状态报告到飞书"""
    ts = _now_utc().strftime("%Y-%m-%d %H:%M:%S")
    status_emoji = "🟢" if status == "active" else "🔴"
    drawdown_pct = f"{max_drawdown:.2%}"

    text = (
        f"🔔 **系统状态报告**\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📅 时间: {ts} UTC\n"
        f"🔄 扫描周期: #{cycle_count}\n"
        f"💵 账户权益: ${equity:.2f}\n"
        f"📂 持仓数: {open_positions}\n"
        f"📊 状态: {status_emoji} {status}\n"
        f"📉 连续亏损: {loss_streak} 次\n"
        f"⚠️ 最大回撤: {drawdown_pct}\n"
        f"━━━━━━━━━━━━━━━━"
    )
    return send_text(text)


def send_scan_result(
    signals_count: int,
    details: list[str] | None = None,
) -> bool:
    """发送扫描结果到飞书"""
    ts = _now_utc().strftime("%Y-%m-%d %H:%M:%S")
    emoji = "✅" if signals_count > 0 else "⚪"

    text = f"{emoji} **扫描完成**\n" f"━━━━━━━━━━━━━━━━\n" f"📅 时间: {ts} UTC\n" f"📊 有效信号: {signals_count} 个"

    if details:
        text += "\n\n📋 信号详情:\n"
        for detail in details:
            text += f"• {detail}\n"

    text += "\n━━━━━━━━━━━━━━━━"
    return send_text(text)


def send_error_alert(error_msg: str, context: str = "") -> bool:
    """发送错误告警到飞书"""
    ts = _now_utc().strftime("%Y-%m-%d %H:%M:%S")
    text = (
        f"🚨 **系统异常**\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📅 时间: {ts} UTC\n"
        f"⚠️ 错误: {error_msg}\n"
        f"{f'📝 上下文: {context}' if context else ''}\n"
        f"━━━━━━━━━━━━━━━━"
    )
    return send_text(text)