"""
OKX合约信号系统 - 桌面程序入口
实时接收OKX K线 → 信号检测 → 飞书通知
"""

import sys
import time
import asyncio
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


async def start_realtime_monitor():
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

        connected = await api.connect(symbols)
        if connected:
            logger.info("[OK] WebSocket connected")
            print("[OK] WebSocket connected")

            # 等待初始数据
            print("\nFetching initial K-line data...")
            await asyncio.sleep(3)

            return api
        else:
            logger.error("[FAIL] WebSocket connection failed")
            print("[FAIL] WebSocket connection failed")
            return None

    except Exception as e:
        logger.error(f"[FAIL] Startup failed: {e}")
        print(f"[FAIL] Startup failed: {e}")
        return None


async def signal_loop_async(api, feishu_enabled):
    """信号检测循环 (异步版本)"""
    from okx_signal_system.config import load_yaml, project_paths
    from okx_signal_system.data.loader import load_symbol_file
    from okx_signal_system.features.indicators import build_feature_frame
    from okx_signal_system.strategy.trend_breakout import StrategyParams, generate_signals
    from okx_signal_system.notification.feishu import feishu_send_signal_card, feishu_send_status_card

    paths = project_paths()
    config = load_yaml(paths.config_dir / "base.yaml")
    symbols = config.get('trading', {}).get('watch_symbols', ['BTC-USDT-SWAP'])

    params = StrategyParams()
    last_status_time = 0
    status_interval = 1800  # 30 minutes
    scan_interval = 900  # 15 minutes scan cycle

    logger.info("Signal monitoring started")
    print("\n" + "=" * 50)
    print("Signal monitoring system started")
    print("Press Ctrl+C to exit")
    print("=" * 50 + "\n")

    while True:
        try:
            # 每个扫描周期：对所有监控币种生成信号
            for inst_id in symbols:
                try:
                    # 尝试从实时数据存储获取数据
                    local_data = api._data_store.load(inst_id)

                    if local_data.empty or len(local_data) < 100:
                        # 实时数据不足，从API同步
                        count = api.sync_from_api(inst_id)
                        if count > 0:
                            logger.info(f"Synced {count} bars for {inst_id}")
                            local_data = api._data_store.load(inst_id)

                    if local_data.empty or len(local_data) < 100:
                        continue

                    # 构建特征并生成信号
                    features = build_feature_frame(local_data, **{
                        k: getattr(params, k)
                        for k in ['fast_ema', 'slow_ema', 'breakout_window', 'atr_window']
                    })
                    signals = generate_signals(features, inst_id=inst_id, params=params)
                    accepted = [s for s in signals if s.accepted]

                    # 每10秒打印状态
                    current_time = time.time()
                    if current_time - last_status_time >= 10:
                        latest = local_data.iloc[-1]
                        print(f"[{time.strftime('%H:%M:%S')}] {inst_id}: "
                              f"close={latest['close']:.2f}")
                        last_status_time = current_time

                    # 检测到有效信号
                    if accepted:
                        signal = accepted[-1]

                        logger.info(f"Signal detected: {signal.side} {inst_id}")
                        print(f"\n*** SIGNAL: {signal.side.upper()} {inst_id} ***")

                        if feishu_enabled:
                            feishu_send_signal_card(
                                inst_id=inst_id,
                                direction=signal.side,
                                qty=0.01,
                                leverage=5.0,
                                entry_price=signal.entry_ref or 0,
                                stop_loss=signal.stop_loss or 0,
                                take_profit=signal.take_profit or 0,
                                reason=signal.reject_reason or "signal_valid",
                            )
                            print("[OK] Feishu notification sent")
                        else:
                            print("[SKIP] Feishu not configured")

                except Exception as e:
                    logger.error(f"Error scanning {inst_id}: {e}")

            # 定期持久化
            api.persist_data()

            # 等待下一个扫描周期
            await asyncio.sleep(scan_interval)

        except KeyboardInterrupt:
            logger.info("User interrupted, exiting")
            print("\n\nExiting...")
            break
        except Exception as e:
            logger.error(f"Loop error: {e}")
            await asyncio.sleep(5)


def main():
    """主函数"""
    print_banner()

    # 检查 Python 依赖
    missing = []
    for lib in ['numpy', 'pandas', 'yaml', 'requests']:
        try:
            __import__(lib)
        except ImportError:
            missing.append(lib)

    if missing:
        print(f"\n[ERROR] 缺少必需的 Python 库: {', '.join(missing)}")
        print(f"请运行: pip install {' '.join(missing)}")
        input("\nPress Enter to exit...")
        return

    # 检查配置
    if not check_config():
        input("\nPress Enter to exit...")
        return

    # 检查飞书
    feishu_enabled = check_feishu()

    print()

    # 使用 asyncio 运行异步代码
    asyncio.run(_main_async(feishu_enabled))


async def _main_async(feishu_enabled):
    """异步主逻辑"""
    # 启动实时监控
    api = await start_realtime_monitor()

    if api:
        try:
            await signal_loop_async(api, feishu_enabled)
        finally:
            await api.disconnect()
            logger.info("Disconnected")
    else:
        print("Failed to start real-time monitor")
        input("\nPress Enter to exit...")


if __name__ == "__main__":
    main()
