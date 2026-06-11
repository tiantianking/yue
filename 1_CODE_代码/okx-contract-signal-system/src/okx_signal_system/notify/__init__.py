"""通知模块：飞书、企业微信、邮件等"""
from __future__ import annotations

from okx_signal_system.notify.feishu import (
    send_error_alert,
    send_scan_result,
    send_signal_alert,
    send_status_report,
    send_text,
)