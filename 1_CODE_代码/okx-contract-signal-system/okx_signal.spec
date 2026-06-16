# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller打包配置 - OKX合约信号系统
"""

import os
from pathlib import Path

block_cipher = None

# 隐藏导入（第三方库内部的动态导入）
hiddenimports = [
    'websocket',
    'websocket._abnf',
    'websocket._core',
    'websocket._exceptions',
    'websocket._handshake',
    'websocket._http',
    'websocket._logging',
    'websocket._socket',
    'websocket._url',
    'websocket.app',
    'websocket.assistant',
    'websocket.client',
    'websocket.compat',
    'websocket.handshake',
    'websocket.http',
    'websocket.logging',
    'websocket.protocol',
    'websocket.server',
    'websocket.socket',
    'websocket.url',
    'websocket.utils',
    'websocket._wsdump',
    'okx',
    'okx.OPENAPI',
    'okx.OPENAPIV5',
    'okx.exceptions',
    'okx.consts',
    'okx.account',
    'okx.trade',
    'okx.market_data',
    'okx.public_data',
    'okx.finance',
    'okx.status',
    'okx.sub_account',
    'okx.rubbish_bin',
    'pyarrow',
    'pyarrow._csv_parser',
    'pyarrow._fs',
    'pyarrow._json',
    'pyarrow._parquet',
    'pyarrow.ipc',
    'pyarrow.json',
    'pyarrow.parquet',
    'pandas._libs.tslibs.timestamps',
    'pandas._libs.ops_dispatch',
    'streamlit.elements',
    'streamlit.runtime',
    'streamlit.web',
    'okx_signal_system.exchange.position_monitor',
    'okx_signal_system.strategy.ensemble',
    'okx_signal_system.ml.pattern_recognition',
    'okx_signal_system.ml.rolling_backtest',
]

datas = [
    ('config', 'config'),
    ('assets', 'assets'),
]

history_root = os.environ.get("JIAOYI_DATA_DIR")
if history_root:
    history_path = Path(history_root)
    history_dir = history_path if history_path.name == "lightweight_history" else history_path / "lightweight_history"
    datas.append((str(history_dir), 'lightweight_history'))

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'matplotlib',
        'PIL',
        'cv2',
        'torch',
        'tensorflow',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='OKXSignalSystem',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,  # Windows控制台程序（保留日志输出）
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/icon.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='OKXSignalSystem',
)

# 打包成单文件（可选，取消注释启用）
# mode = 'build' if len(sys.argv) == 1 else sys.argv[1]
# if mode == 'onefile':
#     mode = 'console' if len(sys.argv) == 1 else sys.argv[1]
# else:
#     mode = 'onedir'
#
# if mode == 'onefile':
#     exe = EXE(
#         pyz,
#         a.scripts,
#         a.binaries,
#         a.zipfiles,
#         a.datas,
#         [],
#         name='OKXSignalSystem',
#         debug=False,
#         bootloader_ignore_signals=False,
#         strip=False,
#         upx=True,
#         console=True,
#         icon='icon.ico',
#     )
