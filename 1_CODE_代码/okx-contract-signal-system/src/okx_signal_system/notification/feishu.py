"""
飞书推送模块（已废弃，请使用 okx_signal_system.notify.feishu）

此文件仅保留用于向后兼容。
新代码应直接导入 okx_signal_system.notify.feishu
"""

import warnings

warnings.warn(
    "okx_signal_system.notification.feishu 已废弃，"
    "请使用 okx_signal_system.notify.feishu",
    DeprecationWarning,
    stacklevel=2,
)

# 重定向所有公共接口到 notify.feishu
from okx_signal_system.notify.feishu import (  # noqa: F401
    feishu_send_text,
    feishu_send_signal_card,
    feishu_send_status_card,
    feishu_test,
    FEISHU_WEBHOOK,
)
