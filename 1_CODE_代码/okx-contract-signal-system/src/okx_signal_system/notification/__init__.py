"""飞书通知模块（重定向到 notify.feishu）"""
from okx_signal_system.notify.feishu import (  # noqa: F401
    feishu_send_text,
    feishu_send_signal_card,
    feishu_test,
    FEISHU_WEBHOOK,
)

__all__ = [
    "feishu_send_text",
    "feishu_send_signal_card",
    "feishu_test",
    "FEISHU_WEBHOOK",
]
