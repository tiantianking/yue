"""
飞书 Webhook 推送模块
"""
from __future__ import annotations

import requests
from datetime import datetime, timezone


# 你的 Webhook 地址
FEISHU_WEBHOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/a5fb1dc8-91a4-483f-afe0-218118a8ab28"


def feishu_send_text(text: str) -> bool:
    """发送纯文本消息到飞书群"""
    payload = {
        "msg_type": "text",
        "content": {"text": text},
    }
    try:
        resp = requests.post(FEISHU_WEBHOOK, json=payload, timeout=10)
        result = resp.json()
        return result.get("code") == 0
    except Exception as e:
        print(f"飞书推送失败: {e}")
        return False


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
    """发送交易信号卡片"""
    direction_emoji = "🟢 做多" if direction == "long" else "🔴 做空"

    payload = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": f"📊 交易信号 - {inst_id}"},
                "template": "red" if direction == "short" else "green",
            },
            "elements": [
                {
                    "tag": "div",
                    "fields": [
                        {"is_short": True, "text": {"tag": "lark_md", "content": f"**方向**\n{direction_emoji}"}},
                        {"is_short": True, "text": {"tag": "lark_md", "content": f"**杠杆**\n{leverage:.1f}x"}},
                        {"is_short": True, "text": {"tag": "lark_md", "content": f"**数量**\n{qty:.4f}"}},
                        {"is_short": True, "text": {"tag": "lark_md", "content": f"**入场价**\n{entry_price:.4f}"}},
                        {"is_short": True, "text": {"tag": "lark_md", "content": f"**止损价**\n{stop_loss:.4f}"}},
                        {"is_short": True, "text": {"tag": "lark_md", "content": f"**止盈价**\n{take_profit:.4f}"}},
                    ],
                },
                {"tag": "hr"},
                {
                    "tag": "note",
                    "elements": [{"tag": "plain_text", "content": f"信号原因: {reason}"}],
                },
            ],
        },
    }
    try:
        resp = requests.post(FEISHU_WEBHOOK, json=payload, timeout=10)
        result = resp.json()
        return result.get("code") == 0
    except Exception as e:
        print(f"飞书推送失败: {e}")
        return False


def feishu_send_status_card(
    equity: float,
    open_positions: int,
    status: str,
    loss_streak: int,
    max_drawdown: float,
    cycle_count: int,
    last_signal_count: int = 0,
) -> bool:
    """发送系统状态卡片"""
    status_emoji = "🟢 正常" if status == "active" else "🔴 暂停"
    drawdown_color = "🔴" if max_drawdown > 0.15 else ("🟡" if max_drawdown > 0.08 else "🟢")

    payload = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": "🔔 OKX交易系统状态报告"},
                "template": "blue",
            },
            "elements": [
                {
                    "tag": "div",
                    "fields": [
                        {"is_short": True, "text": {"tag": "lark_md", "content": f"**账户权益**\n${equity:.2f}"}},
                        {"is_short": True, "text": {"tag": "lark_md", "content": f"**持仓数**\n{open_positions}"}},
                        {"is_short": True, "text": {"tag": "lark_md", "content": f"**系统状态**\n{status_emoji}"}},
                        {"is_short": True, "text": {"tag": "lark_md", "content": f"**连续亏损**\n{loss_streak}次"}},
                        {"is_short": True, "text": {"tag": "lark_md", "content": f"**最大回撤**\n{drawdown_color}{max_drawdown:.2%}"}},
                        {"is_short": True, "text": {"tag": "lark_md", "content": f"**扫描周期**\n#{cycle_count}"}},
                    ],
                },
                {"tag": "hr"},
                {
                    "tag": "note",
                    "elements": [
                        {"tag": "plain_text", "content": f"最后信号数: {last_signal_count} | 推送时间: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC"}
                    ],
                },
            ],
        },
    }
    try:
        resp = requests.post(FEISHU_WEBHOOK, json=payload, timeout=10)
        result = resp.json()
        return result.get("code") == 0
    except Exception as e:
        print(f"飞书推送失败: {e}")
        return False


def feishu_test() -> bool:
    """测试飞书连接"""
    return feishu_send_text("🧪 OKX交易系统连接测试成功！")