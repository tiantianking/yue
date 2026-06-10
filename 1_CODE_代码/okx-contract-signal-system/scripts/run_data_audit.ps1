$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = Join-Path $root "src"
& "D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe" -m okx_signal_system.data.cli @args
