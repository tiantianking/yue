"""
飞书通知验证测试
"""

from okx_signal_system.notification.feishu import (
    feishu_test,
    feishu_send_signal_card,
    feishu_send_status_card,
)
from okx_signal_system.notify.feishu import send_signal_alert, send_status_report


def main():
    print("=" * 50)
    print("飞书通知功能验证")
    print("=" * 50)

    # 1. 测试连接
    print("\n1. 测试飞书连接...")
    result = feishu_test()
    print(f"   连接测试: {'成功' if result else '失败'}")

    # 2. 发送模拟交易信号（卡片格式）
    print("\n2. 发送模拟交易信号（卡片格式）...")
    result1 = feishu_send_signal_card(
        inst_id='BTC-USDT-SWAP',
        direction='long',
        qty=0.01,
        leverage=5.0,
        entry_price=65000.00,
        stop_loss=64000.00,
        take_profit=67000.00,
        reason='测试信号：趋势突破 + EMA多头排列'
    )
    print(f"   信号卡片: {'成功' if result1 else '失败'}")

    # 3. 发送状态报告（卡片格式）
    print("\n3. 发送系统状态报告（卡片格式）...")
    result2 = feishu_send_status_card(
        equity=10500.00,
        open_positions=0,
        status='active',
        loss_streak=0,
        max_drawdown=0.05,
        cycle_count=99,
        last_signal_count=0
    )
    print(f"   状态卡片: {'成功' if result2 else '失败'}")

    # 4. 发送纯文本信号（notify模块）
    print("\n4. 发送纯文本交易信号...")
    result3 = send_signal_alert(
        inst_id='ETH-USDT-SWAP',
        side='short',
        entry_ref=3500.00,
        stop_loss=3550.00,
        take_profit=3400.00,
        qty=0.1,
        leverage=3.0,
        reason='测试：4h趋势空头 + 突破下方支撑'
    )
    print(f"   文本信号: {'成功' if result3 else '失败'}")

    # 5. 发送状态报告（notify模块）
    print("\n5. 发送纯文本状态报告...")
    result4 = send_status_report(
        cycle_count=100,
        equity=10200.00,
        open_positions=1,
        status='active',
        loss_streak=1,
        max_drawdown=0.08
    )
    print(f"   文本状态: {'成功' if result4 else '失败'}")

    # 总结
    print("\n" + "=" * 50)
    all_success = all([result, result1, result2, result3, result4])
    print(f"飞书通知验证结果: {'全部成功' if all_success else '部分失败'}")
    print("=" * 50)

    if all_success:
        print("\n[PASS] 系统已正确接入飞书，会在以下情况发送通知：")
        print("   - 产生有效交易信号时推送信号卡片")
        print("   - 每30分钟推送系统状态卡片")
        print("   - 系统异常时发送告警")
    else:
        print("\n[FAIL] 部分功能失败，请检查飞书Webhook配置")


if __name__ == "__main__":
    main()