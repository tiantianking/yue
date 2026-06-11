"""
OKX合约信号系统 - 桌面程序入口
实时接收OKX K线 → 信号检测 → 飞书通知
"""

import sys
import time
import logging
from pathlib import Path

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('okx_signal.log', encoding='utf-8'),
    ]
)
logger = logging.getLogger(__name__)


def print_banner():
    """打印启动横幅"""
    banner = """
╔═══════════════════════════════════════════════════════╗
║       OKX 合约信号系统 v2.0                            ║
║       实时K线 · 信号检测 · 飞书通知                    ║
╚═══════════════════════════════════════════════════════╝
"""
    print(banner)


def check_config():
    """检查配置文件"""
    from okx_signal_system.config import project_paths

    paths = project_paths()
    config_file = paths.config_dir / "base.yaml"

    if not config_file.exists():
        logger.error(f"配置文件不存在: {config_file}")
        print(f"\n[错误] 配置文件不存在: {config_file}")
        print("请先配置 base.yaml")
        return False

    logger.info(f"配置路径: {config_file}")
    return True


def check_feishu():
    """检查飞书配置"""
    from okx_signal_system.config import load_config

    try:
        config = load_config()
        webhook = config.get('feishu', {}).get('webhook_url', '')

        if webhook and webhook != 'YOUR_WEBHOOK_URL_HERE':
            logger.info("✓ 飞书通知已配置")
            print("✓ 飞书通知已配置")
            return True
        else:
            logger.warning("✗ 飞书Webhook未配置")
            print("✗ 飞书Webhook未配置 (信号通知将不发送)")
            return False
    except Exception as e:
        logger.warning(f"✗ 飞书配置检查失败: {e}")
        print(f"✗ 飞书配置检查失败: {e}")
        return False


def start_realtime_monitor():
    """启动实时监控"""
    from okx_signal_system.exchange.realtime import OKXRealtimeAPI
    from okx_signal_system.config import load_config

    config = load_config()
    symbols = config.get('trading', {}).get('watch_symbols', ['BTC-USDT-SWAP'])

    logger.info(f"监控品种: {symbols}")
    print(f"\n监控品种: {', '.join(symbols)}")

    api = OKXRealtimeAPI()

    try:
        logger.info("正在连接OKX WebSocket...")
        print("正在连接OKX WebSocket...")

        if api.connect():
            logger.info("✓ WebSocket连接成功")
            print("✓ WebSocket连接成功")

            # 初始化数据存储
            api.init_stores(symbols)

            # 等待初始数据
            print("\n正在获取初始K线数据...")
            time.sleep(3)

            return api
        else:
            logger.error("✗ WebSocket连接失败")
            print("✗ WebSocket连接失败")
            return None

    except Exception as e:
        logger.error(f"✗ 启动失败: {e}")
        print(f"✗ 启动失败: {e}")
        return None


def signal_loop(api, feishu_enabled):
    """信号检测循环"""
    from okx_signal_system.exchange.realtime import LiveSignalMonitor
    from okx_signal_system.notification.feishu import feishu_send_signal_card, feishu_send_status_card
    from okx_signal_system.config import load_config

    config = load_config()
    symbols = config.get('trading', {}).get('watch_symbols', ['BTC-USDT-SWAP'])

    monitor = LiveSignalMonitor(api, symbols)

    last_status_time = 0
    status_interval = 1800  # 30分钟

    logger.info("信号监控已启动")
    print("\n" + "=" * 50)
    print("信号监控系统已启动")
    print("按 Ctrl+C 退出")
    print("=" * 50 + "\n")

    while True:
        try:
            # 检测信号
            result = monitor.check_signals()

            # 获取最新数据状态
            for inst_id in symbols:
                store = api.get_store(inst_id)
                if store:
                    bars = store.get_bars('1h', count=5)
                    if bars is not None and len(bars) > 0:
                        latest = bars.iloc[-1]
                        ts = latest.name

                        # 每10秒打印状态
                        current_time = time.time()
                        if current_time - last_status_time >= 10:
                            complete = "✓" if latest.get('complete_1h', False) else "○"
                            print(f"[{time.strftime('%H:%M:%S')}] {inst_id}: "
                                  f"收盘价={latest['close']:.2f} {complete}")
                            last_status_time = current_time

                        # 检测到有效信号
                        if result and result.get('signal', {}).get('side') in ['long', 'short']:
                            signal = result['signal']

                            logger.info(f"检测到信号: {signal['side']} {inst_id}")
                            print(f"\n🚨 检测到信号: {signal['side'].upper()} {inst_id}")

                            if feishu_enabled:
                                feishu_send_signal_card(
                                    inst_id=inst_id,
                                    direction=signal['side'],
                                    qty=result.get('risk', {}).get('qty', 0.01),
                                    leverage=signal.get('leverage', 5),
                                    entry_price=signal.get('entry_ref', 0),
                                    stop_loss=signal.get('stop_loss', 0),
                                    take_profit=signal.get('take_profit', 0),
                                    reason=signal.get('reason', '')
                                )
                                print("✓ 飞书通知已发送")
                            else:
                                print("⚠ 飞书未配置，跳过通知")

            time.sleep(1)

        except KeyboardInterrupt:
            logger.info("用户中断，退出程序")
            print("\n\n正在退出...")
            break
        except Exception as e:
            logger.error(f"循环异常: {e}")
            time.sleep(5)


def main():
    """主函数"""
    print_banner()

    # 检查配置
    if not check_config():
        input("\n按回车键退出...")
        return

    # 检查飞书
    feishu_enabled = check_feishu()

    print()

    # 启动实时监控
    api = start_realtime_monitor()

    if api:
        try:
            signal_loop(api, feishu_enabled)
        finally:
            api.disconnect()
            logger.info("已断开连接")
    else:
        print("未能启动实时监控")
        input("\n按回车键退出...")


if __name__ == "__main__":
    main()