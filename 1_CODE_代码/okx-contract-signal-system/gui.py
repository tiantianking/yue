"""
OKX 信号系统 - Tkinter GUI 主界面
"""

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import threading
import queue
import asyncio
from datetime import datetime, timezone
from pathlib import Path
import sys
import os


# 添加 src/ 到 Python 路径
_project_root = Path(__file__).parent
_src_path = _project_root / "src"
if str(_src_path) not in sys.path:
    sys.path.insert(0, str(_src_path))


def get_resource_path(relative_path):
    """获取资源的绝对路径，兼容开发环境和 PyInstaller 打包环境"""
    try:
        # PyInstaller 创建的临时文件夹路径
        base_path = sys._MEIPASS
    except AttributeError:
        base_path = Path(__file__).parent
    
    return Path(base_path) / relative_path


class GUILogHandler:
    """将日志输出到 GUI 文本框（兼容 logging.Handler 和 file-like 接口）"""
    def __init__(self, log_queue):
        self.log_queue = log_queue
    
    def emit(self, record):
        """添加日志消息到队列（兼容 logging.Handler 接口）
        
        Args:
            record: logging.LogRecord 对象或字符串
        """
        if isinstance(record, str):
            msg = record
            level = "INFO"
        else:
            # 假设是 logging.LogRecord 对象
            msg = record.getMessage()
            level = record.levelname
        
        if msg.strip():
            self.log_queue.put(('log', (msg.strip(), level)))
    
    def write(self, msg):
        """兼容 file-like 接口"""
        if msg.strip():
            self.log_queue.put(('log', (msg.strip(), "INFO")))


class OKXSignalGUI:
    """OKX 信号系统 GUI 主类"""
    
    def __init__(self, root):
        self.root = root
        self.root.title("OKX 信号系统 v3.2")
        self.root.geometry("1000x700")
        
        # 设置窗口图标（如果存在）
        icon_path = get_resource_path("assets/icon.ico")
        if icon_path.exists():
            try:
                self.root.iconbitmap(str(icon_path))
            except Exception:
                pass
        
        # 线程间通信队列
        self.message_queue = queue.Queue()
        self.monitoring = False
        self.monitor_thread = None
        self.log_text = None  # 初始化为 None，等待 create_log_frame 创建
        self.api = None  # API 实例
        self._watched_symbols = []  # 监控币种列表
        self._trained_params = None
        self._startup_quality_report = None
        self._quality_gate_allows_push = False
        
        # 创建界面
        self.create_widgets()
        
        # 启动 GUI 更新循环
        self.update_gui()
    
    def create_widgets(self):
        """创建所有界面组件"""
        # 1. 顶部工具栏
        self.create_toolbar()
        
        # 2. 监控币种列表
        self.create_symbol_frame()
        
        # 3. 持仓监控面板
        self.create_position_frame()

        # 4. 实时信号表格
        self.create_signal_frame()

        # 5. 系统日志
        self.create_log_frame()

        # 6. 状态栏
        self.create_status_bar()
    
    def create_toolbar(self):
        """创建顶部工具栏"""
        toolbar = ttk.Frame(self.root)
        toolbar.pack(fill='x', padx=5, pady=5)
        
        # 连接状态
        self.status_label = ttk.Label(toolbar, text="● 未连接", foreground="red")
        self.status_label.pack(side='left', padx=5)
        
        # 按钮
        self.start_btn = ttk.Button(toolbar, text="启动监控", command=self.start_monitoring)
        self.start_btn.pack(side='left', padx=5)
        
        self.stop_btn = ttk.Button(toolbar, text="停止监控", command=self.stop_monitoring, state='disabled')
        self.stop_btn.pack(side='left', padx=5)
        
        # 右侧信息
        self.time_label = ttk.Label(toolbar, text="")
        self.time_label.pack(side='right', padx=5)
        self.update_time()
    
    def create_symbol_frame(self):
        """创建监控币种列表"""
        symbol_frame = ttk.LabelFrame(self.root, text="监控币种")
        symbol_frame.pack(fill='x', padx=5, pady=5)
        
        # 创建币种列表（使用 Listbox）
        self.symbol_list = tk.Listbox(symbol_frame, height=3, selectmode='none')
        self.symbol_list.pack(fill='x', padx=5, pady=5)
        
        # 添加滚动条
        scrollbar = ttk.Scrollbar(symbol_frame, command=self.symbol_list.yview)
        scrollbar.pack(side='right', fill='y')
        self.symbol_list.config(yscrollcommand=scrollbar.set)
        
        # 加载币种列表（从配置文件）
        self.load_symbols()
    
    def create_position_frame(self):
        """创建持仓监控面板"""
        pos_frame = ttk.LabelFrame(self.root, text="持仓监控（自动止盈止损）")
        pos_frame.pack(fill='x', padx=5, pady=3)

        # 上方：持仓表格
        columns = ('inst_id', 'side', 'entry', 'current', 'sl', 'tp', 'pnl', 'score')
        self.pos_tree = ttk.Treeview(pos_frame, columns=columns, show='headings', height=4)

        self.pos_tree.heading('inst_id', text='合约')
        self.pos_tree.heading('side', text='方向')
        self.pos_tree.heading('entry', text='开仓价')
        self.pos_tree.heading('current', text='现价')
        self.pos_tree.heading('sl', text='止损价')
        self.pos_tree.heading('tp', text='止盈价')
        self.pos_tree.heading('pnl', text='浮盈亏')
        self.pos_tree.heading('score', text='信号评分')

        self.pos_tree.column('inst_id', width=120)
        self.pos_tree.column('side', width=40)
        self.pos_tree.column('entry', width=80)
        self.pos_tree.column('current', width=80)
        self.pos_tree.column('sl', width=80)
        self.pos_tree.column('tp', width=80)
        self.pos_tree.column('pnl', width=80)
        self.pos_tree.column('score', width=60)

        pos_scrollbar = ttk.Scrollbar(pos_frame, orient='vertical', command=self.pos_tree.yview)
        self.pos_tree.configure(yscrollcommand=pos_scrollbar.set)
        self.pos_tree.pack(side='left', fill='both', expand=True, padx=5, pady=3)
        pos_scrollbar.pack(side='right', fill='y', pady=3)

        # 下方：手动注册持仓
        reg_frame = ttk.Frame(pos_frame)
        reg_frame.pack(fill='x', padx=5, pady=3)

        ttk.Label(reg_frame, text="合约:").pack(side='left', padx=2)
        self.reg_inst_id = ttk.Entry(reg_frame, width=14)
        self.reg_inst_id.pack(side='left', padx=2)
        self.reg_inst_id.insert(0, "BTC-USDT-SWAP")

        ttk.Label(reg_frame, text="方向:").pack(side='left', padx=2)
        self.reg_side_var = tk.StringVar(value="long")
        side_combo = ttk.Combobox(reg_frame, textvariable=self.reg_side_var, values=["long", "short"], width=5, state='readonly')
        side_combo.pack(side='left', padx=2)

        ttk.Label(reg_frame, text="开仓价:").pack(side='left', padx=2)
        self.reg_entry_price = ttk.Entry(reg_frame, width=10)
        self.reg_entry_price.pack(side='left', padx=2)

        ttk.Label(reg_frame, text="数量:").pack(side='left', padx=2)
        self.reg_size = ttk.Entry(reg_frame, width=8)
        self.reg_size.pack(side='left', padx=2)

        ttk.Label(reg_frame, text="止损:").pack(side='left', padx=2)
        self.reg_sl = ttk.Entry(reg_frame, width=10)
        self.reg_sl.pack(side='left', padx=2)

        ttk.Label(reg_frame, text="止盈:").pack(side='left', padx=2)
        self.reg_tp = ttk.Entry(reg_frame, width=10)
        self.reg_tp.pack(side='left', padx=2)

        ttk.Label(reg_frame, text="杠杆:").pack(side='left', padx=2)
        self.reg_leverage = ttk.Entry(reg_frame, width=4)
        self.reg_leverage.pack(side='left', padx=2)
        self.reg_leverage.insert(0, "10")

        ttk.Label(reg_frame, text="评分:").pack(side='left', padx=2)
        self.reg_score = ttk.Entry(reg_frame, width=4)
        self.reg_score.pack(side='left', padx=2)
        self.reg_score.insert(0, "7")

        ttk.Button(reg_frame, text="注册持仓", command=self._register_position).pack(side='left', padx=5)
        ttk.Button(reg_frame, text="移除选中", command=self._remove_selected_position).pack(side='left', padx=2)

    def _register_position(self):
        """注册手动开仓的持仓"""
        try:
            from okx_signal_system.exchange.position_monitor import register_manual_position
            record = register_manual_position(
                inst_id=self.reg_inst_id.get().strip(),
                side=self.reg_side_var.get(),
                entry_price=float(self.reg_entry_price.get()),
                size=float(self.reg_size.get()),
                stop_loss=float(self.reg_sl.get()),
                take_profit=float(self.reg_tp.get()),
                leverage=float(self.reg_leverage.get()),
                signal_score=float(self.reg_score.get()),
            )
            self.log(f"✅ 持仓已注册: {record.key} | SL={record.stop_loss:.2f} TP={record.take_profit:.2f}", "INFO")
            self._refresh_position_table()
        except Exception as e:
            self.log(f"❌ 注册持仓失败: {e}", "ERROR")

    def _remove_selected_position(self):
        """移除选中的持仓记录"""
        sel = self.pos_tree.selection()
        if not sel:
            return
        try:
            from okx_signal_system.exchange.position_monitor import PositionRecordStore
            store = PositionRecordStore()
            for item in sel:
                values = self.pos_tree.item(item, 'values')
                inst_id = values[0]
                side = values[1]
                key = f"{inst_id}_{side}"
                store.delete(key)
                self.log(f"已移除持仓记录: {key}", "INFO")
            self._refresh_position_table()
        except Exception as e:
            self.log(f"移除失败: {e}", "ERROR")

    def _refresh_position_table(self):
        """刷新持仓表格"""
        try:
            from okx_signal_system.exchange.position_monitor import PositionRecordStore
            store = PositionRecordStore()
            records = store.load_all()

            # 清空表格
            for item in self.pos_tree.get_children():
                self.pos_tree.delete(item)

            # 填充数据
            for key, rec in records.items():
                self.pos_tree.insert('', 'end', values=(
                    rec.inst_id,
                    '多' if rec.side == 'long' else '空',
                    f"{rec.entry_price:.2f}",
                    '-',  # 现价稍后更新
                    f"{rec.stop_loss:.2f}",
                    f"{rec.take_profit:.2f}",
                    '-',
                    f"{rec.signal_score:.1f}/10" if rec.signal_score else "N/A",
                ))
        except Exception as e:
            self.log(f"刷新持仓表失败: {e}", "WARNING")

    def create_signal_frame(self):
        """创建实时信号表格"""
        signal_frame = ttk.LabelFrame(self.root, text="实时信号")
        signal_frame.pack(fill='both', expand=True, padx=5, pady=5)
        
        # 创建表格
        columns = ('time', 'symbol', 'type', 'price', 'confidence')
        self.signal_tree = ttk.Treeview(signal_frame, columns=columns, show='headings', height=10)
        
        # 设置列标题
        self.signal_tree.heading('time', text='时间')
        self.signal_tree.heading('symbol', text='币种')
        self.signal_tree.heading('type', text='信号类型')
        self.signal_tree.heading('price', text='价格')
        self.signal_tree.heading('confidence', text='置信度')
        
        # 设置列宽
        self.signal_tree.column('time', width=150)
        self.signal_tree.column('symbol', width=150)
        self.signal_tree.column('type', width=100)
        self.signal_tree.column('price', width=100)
        self.signal_tree.column('confidence', width=100)
        
        # 添加滚动条
        tree_scrollbar = ttk.Scrollbar(signal_frame, orient='vertical', command=self.signal_tree.yview)
        self.signal_tree.configure(yscrollcommand=tree_scrollbar.set)
        
        # 布局
        self.signal_tree.pack(side='left', fill='both', expand=True, padx=5, pady=5)
        tree_scrollbar.pack(side='right', fill='y', pady=5)
    
    def create_log_frame(self):
        """创建系统日志文本框"""
        log_frame = ttk.LabelFrame(self.root, text="系统日志")
        log_frame.pack(fill='both', expand=True, padx=5, pady=5)
        
        # 创建文本框（带滚动条）
        self.log_text = scrolledtext.ScrolledText(log_frame, height=15, state='disabled')
        self.log_text.pack(fill='both', expand=True, padx=5, pady=5)
        
        # 配置文本颜色
        self.log_text.tag_config('INFO', foreground='black')
        self.log_text.tag_config('WARNING', foreground='orange')
        self.log_text.tag_config('ERROR', foreground='red')
    
    def create_status_bar(self):
        """创建状态栏"""
        self.status_bar = ttk.Label(self.root, text="就绪", relief='sunken', anchor='w')
        self.status_bar.pack(fill='x', side='bottom', padx=5, pady=5)
    
    def load_symbols(self):
        """加载监控币种列表"""
        try:
            from okx_signal_system.config import load_config
            config = load_config("base.yaml")
            symbols = config.get('data', {}).get('symbols', ['BTC-USDT-SWAP'])
            
            self.symbol_list.delete(0, 'end')
            for symbol in symbols:
                self.symbol_list.insert('end', f"{symbol}  ✅")
            
            self.log(f"加载了 {len(symbols)} 个监控币种")
        except Exception as e:
            self.log(f"加载币种列表失败: {e}", "ERROR")
            # 使用默认值
            self.symbol_list.insert('end', 'BTC-USDT-SWAP  ✅')
    
    def start_monitoring(self):
        """启动监控"""
        if self.monitoring:
            return

        self.log("正在启动监控...", "INFO")
        self.update_status("正在启动...")

        # 更新按钮状态
        self.start_btn.config(state='disabled')
        self.stop_btn.config(state='normal')

        # 启动自动止盈止损监控器
        try:
            from okx_signal_system.exchange.position_monitor import AutoStopMonitor
            self.auto_stop_monitor = AutoStopMonitor(check_interval=5.0)
            self.auto_stop_monitor.set_on_close_callback(self._on_position_closed)
            self.auto_stop_monitor.start()
            self.log("🛡️ 自动止盈止损监控已启动（每5秒检查）", "INFO")
        except Exception as e:
            self.log(f"⚠️ 自动止盈止损启动失败: {e}", "WARNING")

        # 刷新持仓表
        self._refresh_position_table()

        # 在新线程中启动监控
        self.monitoring = True
        self.monitor_thread = threading.Thread(target=self._run_monitoring, daemon=True)
        self.monitor_thread.start()

    def stop_monitoring(self):
        """停止监控"""
        if not self.monitoring:
            return

        self.log("正在停止监控...", "INFO")
        self.update_status("正在停止...")

        # 停止自动止盈止损监控器
        if hasattr(self, 'auto_stop_monitor') and self.auto_stop_monitor:
            self.auto_stop_monitor.stop()

        # 设置标志位，让后台线程退出
        self.monitoring = False

        # 更新按钮状态
        self.start_btn.config(state='normal')
        self.stop_btn.config(state='disabled')
    
    def _run_monitoring(self):
        """在后台线程中运行监控"""
        try:
            # 创建新的事件循环（因为在线程中）
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            # 运行监控
            loop.run_until_complete(self._monitoring_loop())
        
        except Exception as e:
            self.message_queue.put(('log', (f"监控异常: {e}", "ERROR")))
            self.message_queue.put(('status', 'error'))
        finally:
            self.monitoring = False
            self.message_queue.put(('log', ("监控已停止", "INFO")))
            self.message_queue.put(('status', 'stopped'))
    
    async def _monitoring_loop(self):
        """监控主循环（集成信号检测）"""
        try:
            # 更新状态
            self.message_queue.put(('status', 'connecting'))
            self.message_queue.put(('log', ('正在初始化...', "INFO")))
            
            # 导入监控模块
            from okx_signal_system.exchange.realtime import OKXRealtimeAPI
            
            # 创建 API 实例
            self.api = OKXRealtimeAPI()
            
            # 加载配置
            from okx_signal_system.config import load_config
            config = load_config("base.yaml")
            self._watched_symbols = config.get('data', {}).get('symbols', ['BTC-USDT-SWAP'])
            
            # 历史数据通过 RealtimeDataStore.load() 自动从本地 parquet 加载
            # 预加载到内存缓存（避免首次信号检测时逐个读取磁盘）
            self.message_queue.put(('log', ('正在加载本地历史数据...', "INFO")))
            loaded_count = 0
            for inst_id in self._watched_symbols:
                try:
                    df = self.api._data_store.load(inst_id)
                    if len(df) >= 80:
                        loaded_count += 1
                except Exception:
                    pass
            self.message_queue.put(('log', (f"已加载 {loaded_count} 个币种的历史数据", "INFO")))
            
            # 启动时自动补全数据缺口（断网/几天未开后的数据回补）
            self.message_queue.put(('log', ('正在检查数据缺口并回补...', "INFO")))
            try:
                from okx_signal_system.data.gap_handler import sync_on_startup
                sync_results = sync_on_startup(self._watched_symbols)
                total_bars = sum(r.bars_added for r in sync_results.values())
                total_gaps = sum(r.gaps_filled for r in sync_results.values())
                self.message_queue.put(('log', (f"数据回补完成：{total_gaps} 个缺口，补充 {total_bars} 根 K线", "INFO")))
            except Exception as e:
                self.message_queue.put(('log', (f"数据回补异常: {e}", "WARNING")))
            
            # 对本地没有数据的币种，从 REST API 拉取
            missing = []
            for inst_id in self._watched_symbols:
                try:
                    df = self.api._data_store.load(inst_id)
                    if len(df) < 80:
                        missing.append(inst_id)
                except Exception:
                    missing.append(inst_id)
            
            if missing:
                self.message_queue.put(('log', (f"从 API 同步 {len(missing)} 个缺失币种...", "INFO")))
                for inst_id in missing:
                    try:
                        count = self.api.sync_from_api(inst_id)
                        self.message_queue.put(('log', (f"  {inst_id}: 同步 {count} 根 K线", "INFO")))
                    except Exception as e:
                        self.message_queue.put(('log', (f"  {inst_id}: 同步失败 {e}", "WARNING")))

            # 启动训练质量门：加载历史训练参数，并用本地历史数据做一次训练/验证拆分检查
            try:
                from okx_signal_system.training.startup_quality import run_startup_quality_gate
                self.message_queue.put(('log', ("正在执行启动训练质量门...", "INFO")))
                report = run_startup_quality_gate(symbols=self._watched_symbols, max_symbols=None)
                self._startup_quality_report = report
                self._trained_params = report.strategy_params
                self._quality_gate_allows_push = report.status == "passed"
                summary = report.valid_summary
                self.message_queue.put(('log', (
                    f"启动训练质量门 {report.status}: 验证交易 {summary.get('total_trades', 0)} 笔，"
                    f"PF={summary.get('profit_factor', 0):.2f}，参数={report.selected_params}",
                    "INFO" if report.status == "passed" else "WARNING",
                )))
                if report.stale_symbols:
                    self.message_queue.put(('log', (
                        f"以下币种本地K线较旧，实时推送前会等待新K线: {', '.join(report.stale_symbols[:6])}",
                        "WARNING",
                    )))
                if not self._quality_gate_allows_push:
                    self.message_queue.put(('log', (
                        f"质量门未通过，飞书推送暂停；原因: {', '.join(report.reasons) or 'unknown'}",
                        "WARNING",
                    )))
            except Exception as e:
                self._quality_gate_allows_push = False
                self.message_queue.put(('log', (f"启动训练质量门异常，使用默认参数: {e}", "WARNING")))
            
            # 连接 WebSocket（接收实时更新）
            self.message_queue.put(('log', ('正在连接 WebSocket...', "INFO")))
            connected = await self.api.connect(self._watched_symbols)
            
            if not connected:
                self.message_queue.put(('log', ('WebSocket 连接失败（仅使用本地数据）', "WARNING")))
            else:
                self.message_queue.put(('log', ('WebSocket 已连接，实时数据推送中', "INFO")))
            
            # 更新状态
            self.message_queue.put(('status', 'connected'))
            self.message_queue.put(('log', (f"监控 {len(self._watched_symbols)} 个币种，信号检测已启动", "INFO")))
            
            # 主循环
            last_heartbeat = 0
            last_signal_check = 0
            last_incremental_sync = 0
            checked_bars: dict[str, str] = {}
            
            while self.monitoring:
                current_time = asyncio.get_event_loop().time()
                
                # 每10秒输出一次心跳
                if current_time - last_heartbeat >= 10:
                    self.message_queue.put(('log', ("系统运行中...", "INFO")))
                    last_heartbeat = current_time
                
                # 每30秒检查一次信号
                if current_time - last_signal_check >= 30:
                    await self._check_signals(checked_bars)
                    last_signal_check = current_time
                
                # 每1小时增量同步一次数据（防止WebSocket丢K线）
                if current_time - last_incremental_sync >= 3600:
                    try:
                        from okx_signal_system.data.gap_handler import IncrementalSyncer
                        syncer = IncrementalSyncer()
                        results = syncer.sync_batch(self._watched_symbols, interval_hours=1)
                        if results:
                            total = sum(r.bars_added for r in results.values())
                            if total > 0:
                                self.message_queue.put(('log', (f"增量同步：补充 {total} 根 K线", "INFO")))
                    except Exception as e:
                        self.message_queue.put(('log', (f"增量同步异常: {e}", "WARNING")))
                    last_incremental_sync = current_time
                
                await asyncio.sleep(1)
            
            # 断开连接
            await self.api.disconnect()
            self.message_queue.put(('log', ("WebSocket 已断开", "INFO")))
        
        except asyncio.CancelledError:
            self.message_queue.put(('log', ("监控被取消", "WARNING")))
        except Exception as e:
            self.message_queue.put(('log', (f"监控循环异常: {e}", "ERROR")))
    
    async def _check_signals(self, checked_bars: dict[str, str]):
        """检查各币种的 trading 信号"""
        try:
            from dataclasses import replace
            import pandas as pd
            from okx_signal_system.features.indicators import build_feature_frame
            from okx_signal_system.strategy.trend_breakout import build_signal, StrategyParams
            from okx_signal_system.config import load_config
            from okx_signal_system.ml.regime_adaptive import AdaptiveParamsManager
            from okx_signal_system.training.startup_quality import is_latest_bar_fresh

            # 加载策略参数
            config = load_config("base.yaml")
            strategy_cfg = config.get('strategy', {})

            # 从配置取参数（列表取第一个，标量直接取）
            def _first_or_val(val, default):
                if isinstance(val, list) and len(val) > 0:
                    return val[0]
                return val if val is not None else default

            base_params = StrategyParams(
                fast_ema=int(_first_or_val(strategy_cfg.get('fast_ema'), 20)),
                slow_ema=int(_first_or_val(strategy_cfg.get('slow_ema'), 60)),
                breakout_window=int(_first_or_val(strategy_cfg.get('breakout_window'), 40)),
                atr_stop_mult=float(_first_or_val(strategy_cfg.get('atr_stop_mult'), 2.0)),
                take_profit_mult=float(_first_or_val(strategy_cfg.get('take_profit_mult'), 2.0)),
                max_hold_bars=int(_first_or_val(strategy_cfg.get('max_hold_bars'), 48)),
                atr_window=int(_first_or_val(strategy_cfg.get('atr_window'), 14)),
            )
            trained_params = getattr(self, '_trained_params', None)
            base_params = trained_params if trained_params is not None else base_params

            # 初始化自适应参数管理器（单例）
            if not hasattr(self, '_adaptive_manager'):
                self._adaptive_manager = AdaptiveParamsManager()
            
            for inst_id in self._watched_symbols:
                # 从数据存储加载
                df = self.api._data_store.load(inst_id)
                
                if len(df) < 80:
                    continue  # 数据不足（至少需要80根K线计算EMA60+突破位）
                
                # 检查是否有新K线
                last_ts = str(df["ts"].iloc[-1]) if len(df) > 0 else ""
                if checked_bars.get(inst_id) == last_ts:
                    continue  # 没有新数据
                
                checked_bars[inst_id] = last_ts

                if not is_latest_bar_fresh(df, max_lag_hours=3.0):
                    self.message_queue.put(('log', (f"{inst_id} 最新K线超过3小时，等待实时数据后再发信号", "WARNING")))
                    continue
                
                # 计算特征
                try:
                    params = base_params
                    features = build_feature_frame(
                        df,
                        fast_ema=params.fast_ema,
                        slow_ema=params.slow_ema,
                        breakout_window=params.breakout_window,
                        atr_window=params.atr_window,
                    )
                except Exception as e:
                    self.message_queue.put(('log', (f"特征计算失败 {inst_id}: {e}", "WARNING")))
                    continue

                # 环境只用于降分/降杠杆；策略参数使用历史训练冻结参数
                regime, _adaptive_params = self._adaptive_manager.update_regime(features)
                score_penalty = self._adaptive_manager.get_score_penalty()
                leverage_factor = self._adaptive_manager.get_leverage_factor()
                
                # 取最后一行检测信号
                last_row = features.iloc[-1]
                
                # 检查关键列是否有效
                if pd.isna(last_row.get("atr")) or pd.isna(last_row.get("breakout_high")):
                    continue
                
                signal = build_signal(
                    last_row, inst_id=inst_id, params=params,
                    frame=features, idx=len(features) - 1
                )
                
                if signal.accepted:
                    # 时间格式：检测时间 + K线时间（解决历史数据时间偏移问题）
                    detect_time = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')
                    kline_time = signal.ts.strftime('%Y-%m-%d %H:%M') if hasattr(signal.ts, 'strftime') else str(signal.ts)
                    # 如果K线时间与检测时间差超过2小时，说明是历史数据回放，同时显示两个时间
                    ts_str = f"{detect_time}" if kline_time == detect_time else f"{detect_time} (K线{kline_time})"

                    # P2: 环境自适应 - 应用杠杆调整
                    regime_cn = self._adaptive_manager.get_regime_name_cn()

                    # P4: 多策略投票（用策略评分作为base_score）
                    from okx_signal_system.strategy.ensemble import ensemble_vote
                    base_score_val = signal.signal_score if (signal.signal_score and not pd.isna(signal.signal_score)) else 5.0
                    ensemble_result = ensemble_vote(
                        last_row, params, features, len(features) - 1,
                        base_score=base_score_val,
                    )

                    # 计算有效评分（综合风控+投票+环境惩罚）
                    effective_score = base_score_val

                    # 投票调整
                    if ensemble_result.final_side == "flat":
                        self.message_queue.put(('log', (
                            f"🗳️ 多策略投票否决: {signal.inst_id} | {ensemble_result.details}", "WARNING"
                        )))
                        effective_score = max(1.0, effective_score - 3.0)
                    elif ensemble_result.final_side != signal.side:
                        effective_score = max(1.0, effective_score - 1.5)
                        self.message_queue.put(('log', (
                            f"⚠️ 投票方向不一致: 信号{signal.side} 投票{ensemble_result.final_side} | {ensemble_result.details}", "WARNING"
                        )))
                    else:
                        # 投票一致 → 使用投票增强后的评分
                        effective_score = ensemble_result.final_score

                    # P2: 应用环境评分惩罚
                    if score_penalty < 0:
                        effective_score = max(1.0, effective_score + score_penalty)

                    # 风控校验：使用投票+环境后的综合分决定建议杠杆
                    from okx_signal_system.risk.model import validate_signal, Ledger, RiskConfig
                    max_lev = float(_first_or_val(config.get('risk', {}).get('max_leverage'), 10.0))
                    max_lev_adjusted = max(1.0, min(10.0, max_lev * leverage_factor))
                    risk_config = RiskConfig(max_leverage=max_lev_adjusted)
                    ledger = Ledger(inst_id=inst_id, init_capital=10000, equity=10000)
                    signal = replace(signal, signal_score=effective_score)
                    decision = validate_signal(signal, ledger, risk_config)

                    signal_type = f"{'多' if signal.side == 'long' else '空'}头突破"
                    leverage_text = f"{decision.leverage_used:.1f}x" if decision.accepted and decision.leverage_used else "N/A"
                    rr_text = f"{decision.risk_reward_ratio:.1f}:1" if decision.risk_reward_ratio else ""

                    # 在信号类型中加入环境和投票信息
                    regime_short = {
                        "high_vol_trend": "🔥高波趋势",
                        "low_vol_trend": "📈低波趋势",
                        "high_vol_range": "⚡高波震荡",
                        "low_vol_range": "😴低波震荡",
                        "unknown": "❓未知",
                    }.get(regime, "")

                    # 投票标记
                    vote_mark = "🗳️" if ensemble_result.approval_rate >= 0.7 else ("⚠️" if ensemble_result.approval_rate >= 0.5 else "❌")

                    self.message_queue.put(('signal', {
                        'time': ts_str,
                        'symbol': signal.inst_id,
                        'type': f"{signal_type} {leverage_text} {rr_text} {regime_short} {vote_mark}",
                        'price': f"{signal.entry_ref:.2f}" if signal.entry_ref else "N/A",
                        'confidence': f"{effective_score:.1f}/10",
                    }))
                    
                    status = "✅ 风控通过" if decision.accepted else f"❌ 风控拒绝({decision.reason})"
                    self.message_queue.put(('log', (
                        f"🚨 信号: {signal.inst_id} {signal.side.upper()} @ {signal.entry_ref:.2f} "
                        f"| 杠杆{leverage_text} | 盈亏比{rr_text} | {status}", "INFO"
                    )))
                    
                    # 推送飞书（仅风控通过 + 综合评分≥6的高质量信号才推送，减少噪音）
                    if decision.accepted and effective_score >= 6.0 and getattr(self, '_quality_gate_allows_push', False):
                        try:
                            from okx_signal_system.notify.feishu import send_signal_alert
                            send_signal_alert(
                                inst_id=signal.inst_id,
                                side=signal.side,
                                entry_ref=signal.entry_ref,
                                stop_loss=signal.stop_loss,
                                take_profit=signal.take_profit,
                                qty=decision.qty or 0,
                                leverage=decision.leverage_used or decision.leverage_cap,
                                reason=",".join(signal.reason_codes),
                                signal_score=effective_score,
                                risk_reward_ratio=decision.risk_reward_ratio,
                                stop_reason=decision.stop_reason,
                                tp_reason=decision.tp_reason,
                                max_loss_pct=decision.max_position_loss_pct,
                                kline_time=kline_time,
                            )
                            self.message_queue.put(('log', (f"📤 飞书推送已发送 (评分{effective_score:.1f}≥6)", "INFO")))
                        except Exception as e:
                            self.message_queue.put(('log', (f"飞书推送失败: {e}", "WARNING")))
                    elif decision.accepted and effective_score < 6.0:
                        self.message_queue.put(('log', (f"🔕 低分信号不推送飞书 (评分{effective_score:.1f}<6)", "INFO")))
                    elif decision.accepted and effective_score >= 6.0:
                        self.message_queue.put(('log', ("质量门未通过，高分候选信号暂不推送飞书", "WARNING")))
        
        except Exception as e:
            self.message_queue.put(('log', (f"信号检测异常: {e}", "ERROR")))
    
    def update_gui(self):
        """定期更新 GUI（每 100ms 调用一次）"""
        # 处理队列中的消息
        while not self.message_queue.empty():
            try:
                msg_type, data = self.message_queue.get_nowait()

                if msg_type == 'log':
                    # 添加日志（data 格式为 (message, level)）
                    msg, level = data
                    self.log(msg, level)

                elif msg_type == 'signal':
                    # 添加信号到表格
                    self.add_signal(data)

                elif msg_type == 'status':
                    # 更新连接状态
                    self.update_connection_status(data)

                elif msg_type == 'position_closed':
                    # 持仓被自动平仓
                    self._handle_position_closed(data)

            except queue.Empty:
                break
            except (ValueError, TypeError) as e:
                # 消息格式错误，记录并继续处理
                print(f"[ERROR] 消息格式错误: {e}, data={data}")

        # 每5秒刷新一次持仓表
        if self.monitoring and hasattr(self, '_last_pos_refresh'):
            import time as _time
            if _time.time() - self._last_pos_refresh > 5:
                self._refresh_position_table()
                self._last_pos_refresh = _time.time()
        elif self.monitoring:
            import time as _time
            self._last_pos_refresh = _time.time()

        # 100ms 后再次调用
        self.root.after(100, self.update_gui)
    
    def log(self, message, level="INFO"):
        """添加日志到文本框"""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        formatted_msg = f"[{timestamp}] [{level}] {message}\n"
        
        # 如果 log_text 还未初始化，直接打印到控制台
        if self.log_text is None:
            print(formatted_msg.strip())
            return
        
        # 启用文本框，插入文本，然后禁用
        self.log_text.config(state='normal')
        self.log_text.insert('end', formatted_msg, level)
        self.log_text.see('end')
        self.log_text.config(state='disabled')
    
    def add_signal(self, signal_data):
        """添加新信号到表格"""
        self.signal_tree.insert('', 'end', values=(
            signal_data.get('time', ''),
            signal_data.get('symbol', ''),
            signal_data.get('type', ''),
            signal_data.get('price', ''),
            signal_data.get('confidence', '')
        ))

        # 只保留最近 100 条
        children = self.signal_tree.get_children()
        if len(children) > 100:
            self.signal_tree.delete(children[0])

    def _on_position_closed(self, close_result):
        """持仓被自动平仓的回调（从监控线程调用）"""
        # 通过消息队列传递到GUI线程
        self.message_queue.put(('position_closed', {
            'inst_id': close_result.inst_id,
            'side': close_result.side,
            'entry_price': close_result.entry_price,
            'exit_price': close_result.exit_price,
            'exit_reason': close_result.exit_reason,
            'net_pnl': close_result.net_pnl,
            'net_pnl_pct': close_result.net_pnl_pct,
            'total_costs': close_result.total_costs,
            'signal_score': close_result.signal_score,
        }))

    def _handle_position_closed(self, data):
        """在GUI线程中处理平仓事件"""
        reason_text = "止损" if data['exit_reason'] == 'stop_loss' else "止盈"
        side_text = "多" if data['side'] == 'long' else "空"
        pnl_emoji = "📈" if data['net_pnl'] >= 0 else "📉"

        self.log(
            f"{pnl_emoji} 自动{reason_text}平仓: {data['inst_id']} {side_text}头 "
            f"| 开仓{data['entry_price']:.2f} → 平仓{data['exit_price']:.2f} "
            f"| 净盈亏 {data['net_pnl']:+.4f} USDT ({data['net_pnl_pct']:+.2%}) "
            f"| 总费用 {data['total_costs']:.4f}",
            "WARNING" if data['exit_reason'] == 'stop_loss' else "INFO"
        )

        # 刷新持仓表
        self._refresh_position_table()
    
    def update_connection_status(self, status):
        """更新连接状态显示"""
        if status == 'connected':
            self.status_label.config(text="● 已连接", foreground="green")
            self.update_status("运行正常")
        elif status == 'connecting':
            self.status_label.config(text="● 连接中...", foreground="orange")
            self.update_status("正在连接...")
        elif status == 'error':
            self.status_label.config(text="● 错误", foreground="red")
            self.update_status("连接错误")
        elif status == 'stopped':
            self.status_label.config(text="● 未连接", foreground="red")
            self.update_status("已停止")
    
    def update_status(self, message):
        """更新状态栏"""
        self.status_bar.config(text=message)
    
    def update_time(self):
        """更新时间显示"""
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        self.time_label.config(text=current_time)
        
        # 1秒后再次调用
        self.root.after(1000, self.update_time)
    
    def on_closing(self):
        """窗口关闭事件"""
        if self.monitoring:
            if messagebox.askokcancel("退出", "监控正在运行，确定要退出吗？"):
                self.stop_monitoring()
                self.root.destroy()
        else:
            self.root.destroy()


def start_gui():
    """启动 GUI"""
    root = tk.Tk()
    app = OKXSignalGUI(root)

    # 绑定窗口关闭事件
    root.protocol("WM_DELETE_WINDOW", app.on_closing)

    # 启动主循环
    root.mainloop()


if __name__ == '__main__':
    start_gui()
