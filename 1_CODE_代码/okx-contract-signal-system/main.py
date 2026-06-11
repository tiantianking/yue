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
+=========================================================+
|       OKX Signal System v2.0                            |
|       Real-time K-line | Signal Detection | Feishu       |
+=========================================================+
"""
    print(banner)


def check_config():
    """检查配置文件"""
    from okx_signal_system.config import project_paths

    paths = project_paths()
    config_file = paths.config_dir / "base.yaml"

    if not config_file.exists():
        logger.error(f"Config file not found: {config_file}")
        print(f"\n[ERROR] Config file not found: {config_file}")
        print("Please configure base.yaml first")
        return False

    logger.info(f"Config path: {config_file}")
    return True


def check_feishu():
    """检查飞书配置"""
    from okx_signal_system.config import load_yaml, project_paths

    try:
        paths = project_paths()
        config = load_yaml(paths.config_dir / "base.yaml")
        webhook = config.get('feishu', {}).get('webhook_url', '')

        if webhook and webhook != 'YOUR_WEBHOOK_URL_HERE':
            logger.info("[OK] Feishu configured")
            print("[OK] Feishu configured")
            return True
        else:
            logger.warning("[FAIL] Feishu webhook not configured")
            print("[FAIL] Feishu webhook not configured (alerts will not be sent)")
            return False
    except Exception as e:
        logger.warning(f"[FAIL] Feishu check failed: {e}")
        print(f"[FAIL] Feishu check failed: {e}")
        return False


def start_realtime_monitor():
    """启动实时监控"""
    from okx_signal_system.exchange.realtime import OKXRealtimeAPI
    from okx_signal_system.config import load_yaml, project_paths

    paths = project_paths()
    config = load_yaml(paths.config_dir / "base.yaml")
    symbols = config.get('trading', {}).get('watch_symbols', ['BTC-USDT-SWAP'])

    logger.info(f"Monitoring symbols: {symbols}")
    print(f"\nMonitoring symbols: {', '.join(symbols)}")

    api = OKXRealtimeAPI()

    try:
        logger.info("Connecting to OKX WebSocket...")
        print("Connecting to OKX WebSocket...")

        if api.connect():
            logger.info("[OK] WebSocket connected")
            print("[OK] WebSocket connected")

            # 初始化数据存储
            api.init_stores(symbols)

            # 等待初始数据
            print("\nFetching initial K-line data...")
            time.sleep(3)

            return api
        else:
            logger.error("[FAIL] WebSocket connection failed")
            print("[FAIL] WebSocket connection failed")
            return None

    except Exception as e:
        logger.error(f"[FAIL] Startup failed: {e}")
        print(f"[FAIL] Startup failed: {e}")
        return None


def signal_loop(api, feishu_enabled):
    """信号检测循环"""
    from okx_signal_system.exchange.realtime import LiveSignalMonitor
    from okx_signal_system.notification.feishu import feishu_send_signal_card, feishu_send_status_card
    from okx_signal_system.config import load_yaml, project_paths

    paths = project_paths()
    config = load_yaml(paths.config_dir / "base.yaml")
    symbols = config.get('trading', {}).get('watch_symbols', ['BTC-USDT-SWAP'])

    monitor = LiveSignalMonitor(api, symbols)

    last_status_time = 0
    status_interval = 1800  # 30 minutes

    logger.info("Signal monitoring started")
    print("\n" + "=" * 50)
    print("Signal monitoring system started")
    print("Press Ctrl+C to exit")
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
                            complete = "[OK]" if latest.get('complete_1h', False) else "[..]"
                            print(f"[{time.strftime('%H:%M:%S')}] {inst_id}: "
                                  f"close={latest['close']:.2f} {complete}")
                            last_status_time = current_time

                        # 检测到有效信号
                        if result and result.get('signal', {}).get('side') in ['long', 'short']:
                            signal = result['signal']

                            logger.info(f"Signal detected: {signal['side']} {inst_id}")
                            print(f"\n*** SIGNAL: {signal['side'].upper()} {inst_id} ***")

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
                                print("[OK] Feishu notification sent")
                            else:
                                print("[SKIP] Feishu not configured")

            time.sleep(1)

        except KeyboardInterrupt:
            logger.info("User interrupted, exiting")
            print("\n\nExiting...")
            break
        except Exception as e:
            logger.error(f"Loop error: {e}")
            time.sleep(5)


def main():
    """主函数"""
    print_banner()

    # 检查配置
    if not check_config():
        input("\nPress Enter to exit...")
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
            logger.info("Disconnected")
    else:
        print("Failed to start real-time monitor")
        input("\nPress Enter to exit...")


if __name__ == "__main__":
    main()