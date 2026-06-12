"""
OKX合约信号系统 - 桌面程序入口
实时接收OKX K线 → 信号检测 → 飞书通知

启动模式：
1. 双击exe / python main.py → GUI 模式（默认）
2. python main.py --cli → 命令行模式
"""
from __future__ import annotations

import sys
import io
import os
import time
import signal
import asyncio
import logging
from pathlib import Path
from datetime import datetime, timezone

# ============================================================
# PyInstaller console=False 兼容：防止 stdout/stderr 为 None 崩溃
# ============================================================
if sys.platform == 'win32' and sys.stdout is not None:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
if sys.platform == 'win32' and sys.stderr is not None:
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

if sys.stdout is None:
    sys.stdout = io.StringIO()
if sys.stderr is None:
    sys.stderr = io.StringIO()


def safe_input(prompt=""):
    """安全的 input，在 PyInstaller 打包后的 .exe 中自动等待"""
    try:
        return input(prompt)
    except (RuntimeError, EOFError):
        time.sleep(5)
        return ""

# 添加 src/ 到 Python 路径
APP_VERSION = "v3"
_project_root = Path(__file__).parent
_runtime_root = Path(sys.executable).parent if getattr(sys, "frozen", False) else _project_root
_src_path = _project_root / "src"
if str(_src_path) not in sys.path:
    sys.path.insert(0, str(_src_path))

# ============================================================
# 依赖检查
# ============================================================
def check_dependencies() -> bool:
    """检查必需的第三方库是否已安装"""
    required = [
        ("numpy", "numpy"),
        ("pandas", "pandas"),
        ("yaml", "pyyaml"),
        ("requests", "requests"),
    ]
    missing = []
    for module_name, package_name in required:
        try:
            __import__(module_name)
        except ImportError:
            missing.append(package_name)

    if missing:
        print(f"\n[ERROR] 缺少必需的 Python 库: {', '.join(missing)}")
        print(f"  请运行: pip install {' '.join(missing)}")
        return False
    return True

if not check_dependencies():
    safe_input("\n按 Enter 退出...")
    sys.exit(1)

# ============================================================
# 加载 .env 文件
# ============================================================
def load_env_file() -> None:
    """手动加载 .env 文件到环境变量"""
    if hasattr(sys, '_MEIPASS'):
        base_path = Path(sys.executable).parent
    else:
        base_path = _project_root

    env_file = base_path / ".env"

    if not env_file.exists():
        env_file = _runtime_root / ".env"

    if not env_file.exists():
        return

    print(f"[INFO] 加载 .env 配置文件: {env_file}")
    with open(env_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip("'").strip('"')
                if key and not os.environ.get(key):
                    os.environ[key] = value

load_env_file()

# ============================================================
# 日志配置
# ============================================================
def setup_logging() -> logging.Logger:
    """配置日志：仅文件日志（GUI模式无控制台）"""
    log_dir = _runtime_root / "logs"
    log_dir.mkdir(exist_ok=True)

    log_file = log_dir / f"okx_signal_{datetime.now(timezone.utc).strftime('%Y%m%d')}.log"

    handlers = [
        logging.FileHandler(log_file, encoding='utf-8'),
    ]
    # 仅在有真实控制台时添加 StreamHandler
    if not isinstance(sys.stdout, io.StringIO):
        handlers.append(logging.StreamHandler(sys.stdout))

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        handlers=handlers,
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)

    return logging.getLogger(__name__)


logger = setup_logging()

# ============================================================
# PID 管理
# ============================================================
# 全局锁文件句柄（保持到进程退出）
_lock_file = None

def check_pid_file() -> bool:
    """使用文件锁防止重复启动（比 PID 文件更可靠，无编码/权限问题）"""
    global _lock_file
    lock_path = _runtime_root / "okx_signal.lock"
    try:
        if sys.platform == 'win32':
            import msvcrt
            _lock_file = open(lock_path, 'w')
            msvcrt.locking(_lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            _lock_file.write(str(os.getpid()))
            _lock_file.flush()
            logger.info(f"获取锁成功 (PID: {os.getpid()})")
            return True
        else:
            import fcntl
            _lock_file = open(lock_path, 'w')
            fcntl.flock(_lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            _lock_file.write(str(os.getpid()))
            _lock_file.flush()
            return True
    except (OSError, IOError):
        if _lock_file:
            _lock_file.close()
            _lock_file = None
        logger.error("系统已在运行（无法获取文件锁）")
        return False


def cleanup_pid_file() -> None:
    """释放文件锁并清理"""
    global _lock_file
    if _lock_file:
        try:
            _lock_file.close()
        except Exception:
            pass
        _lock_file = None
    lock_path = _runtime_root / "okx_signal.lock"
    if lock_path.exists():
        try:
            lock_path.unlink()
        except Exception:
            pass


# ============================================================
# 命令行模式
# ============================================================
def check_environment() -> bool:
    """检查环境变量和配置"""
    is_simulated = os.environ.get("OKX_IS_SIMULATED", "true").lower() != "false"

    if is_simulated:
        logger.info("运行在模拟模式")
    else:
        required_env = ["OKX_API_KEY", "OKX_SECRET_KEY", "OKX_PASSPHRASE"]
        missing = [v for v in required_env if not os.environ.get(v)]
        if missing:
            logger.error(f"缺少环境变量: {', '.join(missing)}")
            return False

    return True


async def start_realtime_monitor() -> object | None:
    """启动实时监听（带重试）"""
    from okx_signal_system.exchange.realtime import OKXRealtimeAPI
    from okx_signal_system.config import load_config

    config = load_config("base.yaml")
    symbols = config.get('data', {}).get('symbols', ['BTC-USDT-SWAP'])

    logger.info(f"监听币种: {symbols}")
    print(f"\n监听币种: {', '.join(symbols)}")

    api = OKXRealtimeAPI()

    max_retries = 3
    for attempt in range(max_retries):
        try:
            logger.info(f"正在连接 OKX WebSocket... (尝试 {attempt + 1}/{max_retries})")
            print(f"正在连接 OKX WebSocket... (尝试 {attempt + 1}/{max_retries})")

            connected = await api.connect(symbols)
            if connected:
                logger.info("[OK] WebSocket 已连接")
                print("[OK] WebSocket 已连接")
                return api
            else:
                logger.error("[FAIL] WebSocket 连接失败")
                print("[FAIL] WebSocket 连接失败")

        except Exception as e:
            logger.error(f"[FAIL] 启动失败: {e}")
            print(f"[FAIL] 启动失败: {e}")

        if attempt < max_retries - 1:
            wait_time = 2 ** attempt
            logger.info(f"{wait_time}秒后重试...")
            print(f"{wait_time}秒后重试...")
            await asyncio.sleep(wait_time)

    return None


async def signal_detection_loop(api, symbols: list[str], feishu_enabled: bool) -> None:
    """信号检测循环"""
    from okx_signal_system.exchange.realtime import LiveSignalMonitor
    from okx_signal_system.notify.feishu import send_signal_alert, send_text

    # 构建信号回调：信号通过风控后自动推飞书
    def on_signal(signal, decision):
        """信号回调：推送到飞书"""
        try:
            if not feishu_enabled:
                return
            send_signal_alert(
                inst_id=signal.inst_id,
                side=signal.side,
                entry_ref=signal.entry_ref or 0,
                stop_loss=signal.stop_loss or 0,
                take_profit=signal.take_profit or 0,
                qty=0.01,
                leverage=decision.leverage_used if hasattr(decision, 'leverage_used') else 5.0,
                reason=", ".join(signal.reason_codes) if signal.reason_codes else "",
                signal_score=getattr(decision, 'signal_score', None),
                risk_reward_ratio=getattr(decision, 'risk_reward_ratio', None),
                max_loss_pct=getattr(decision, 'max_loss_pct', None),
            )
            logger.info(f"飞书推送: {signal.inst_id} {signal.side}")
        except Exception as e:
            logger.error(f"飞书推送失败: {e}")

    monitor = LiveSignalMonitor(api, signal_callback=on_signal, risk_config=None)

    logger.info("信号监控系统已启动")
    print("\n" + "=" * 50)
    print("信号监控系统已启动")
    print("按 Ctrl+C 退出")
    print("=" * 50 + "\n")

    # 推送启动通知到飞书
    if feishu_enabled:
        try:
            from okx_signal_system.notify.feishu import send_text
            send_text(f"🟢 OKX信号系统已启动\n监控 {len(symbols)} 个币种\n模式: {'模拟' if os.environ.get('OKX_IS_SIMULATED', 'true').lower() != 'false' else '实盘'}")
        except Exception as e:
            logger.error(f"启动通知推送失败: {e}")

    try:
        await monitor.start()
        last_status_time = 0
        while True:
            current_time = time.time()
            if current_time - last_status_time >= 10:
                print(f"[{time.strftime('%H:%M:%S')}] 系统运行中... 监控 {len(symbols)} 个币种")
                last_status_time = current_time
            await asyncio.sleep(1)

    except KeyboardInterrupt:
        logger.info("用户中断，正在退出...")
        print("\n\n正在退出...")
    except Exception as e:
        logger.error(f"监控异常: {e}")
        print(f"\n[ERROR] 监控异常: {e}")
    finally:
        monitor.stop()
        logger.info("监控已停止")


async def main_async() -> None:
    """异步主逻辑"""
    if not check_environment():
        safe_input("\n按 Enter 退出...")
        return

    if not check_pid_file():
        safe_input("\n按 Enter 退出...")
        return

    try:
        api = await start_realtime_monitor()

        if api:
            try:
                print("\n正在获取初始 K 线数据...")
                await asyncio.sleep(3)

                from okx_signal_system.config import load_config
                config = load_config("base.yaml")
                symbols = config.get('data', {}).get('symbols', ['BTC-USDT-SWAP'])
                feishu_enabled = config.get('feishu', {}).get('enabled', True)

                await signal_detection_loop(api, symbols, feishu_enabled)

            finally:
                await api.disconnect()
                logger.info("已断开连接")
                print("\n已断开连接")
        else:
            print("启动实时监听失败")
            safe_input("\n按 Enter 退出...")

    finally:
        cleanup_pid_file()


def main_cli() -> None:
    """命令行模式主函数"""
    print("\n+=========================================================+")
    print(f"|       OKX Signal System {APP_VERSION:<31}|")
    print("|       Real-time K-line | Signal Detection | Feishu       |")
    print("+=========================================================+\n")

    def signal_handler(sig, frame):
        logger.info(f"收到信号 {sig}，正在退出...")
        print("\n\n正在退出...")
        cleanup_pid_file()
        sys.exit(0)

    if sys.platform != "win32":
        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)

    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        logger.info("用户中断")
    except Exception as e:
        logger.error(f"系统异常退出: {e}")
        print(f"\n[ERROR] 系统异常退出: {e}")
    finally:
        cleanup_pid_file()


# ============================================================
# 入口：默认 GUI，--cli 走命令行
# ============================================================
def main() -> None:
    """主函数 - 默认 GUI 模式"""
    # 简单参数检查（不用 argparse 避免无控制台异常）
    use_cli = '--cli' in sys.argv

    if use_cli:
        main_cli()
    else:
        try:
            from gui import start_gui
            start_gui()
        except ImportError as e:
            logger.warning(f"无法导入 GUI 模块: {e}，回退到命令行模式")
            main_cli()
        except Exception as e:
            logger.error(f"启动 GUI 失败: {e}")
            sys.exit(1)


def main() -> None:
    use_cli = "--cli" in sys.argv

    if use_cli:
        main_cli()
        return

    if not check_environment():
        safe_input("\nPress Enter to exit...")
        return
    if not check_pid_file():
        safe_input("\nPress Enter to exit...")
        return

    try:
        from gui import start_gui

        start_gui()
    except ImportError as exc:
        logger.warning("GUI import failed: %s; falling back to CLI", exc)
        cleanup_pid_file()
        main_cli()
    except Exception as exc:
        logger.error("GUI startup failed: %s", exc)
        sys.exit(1)
    finally:
        cleanup_pid_file()


def global_exception_handler(exc_type, exc_value, exc_traceback):
    """全局异常处理"""
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    logger.critical("未捕获的异常:", exc_info=(exc_type, exc_value, exc_traceback))


sys.excepthook = global_exception_handler

if __name__ == "__main__":
    main()
