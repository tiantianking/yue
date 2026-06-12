@echo off
setlocal EnableDelayedExpansion
title OKX Signal System

echo ========================================
echo  OKX Signal System — v2.0
echo ========================================
echo.

REM ============================================================
REM 确定正确的 Python 解释器
REM 优先级：py launcher > 显式路径 > 系统 PATH 中的 python
REM ============================================================
set "PYTHON_EXE="

REM 1. 尝试 py launcher（最可靠，自动找到正确版本）
py --version > nul 2>&1
if not errorlevel 1 (
    set "PYTHON_EXE=py"
    echo [INFO] Using Python Launcher (py)
    goto :python_found
)

REM 2. 尝试 Python 3.12 显式路径
if exist "C:\Users\26492\AppData\Local\Programs\Python\Python312\python.exe" (
    set "PYTHON_EXE=C:\Users\26492\AppData\Local\Programs\Python\Python312\python.exe"
    echo [INFO] Using Python 3.12 (explicit path)
    goto :python_found
)

REM 3. 尝试系统 PATH 中的 python
python --version > nul 2>&1
if not errorlevel 1 (
    set "PYTHON_EXE=python"
    echo [INFO] Using system python from PATH
    goto :python_found
)

echo [ERROR] Python not found!
echo.
echo Install Python 3.8+ from:
echo   https://www.python.org/downloads/
echo.
echo Make sure to check "Add Python to PATH" during install
pause
exit /b 1

:python_found
for /f "tokens=2" %%i in ('%PYTHON_EXE% --version 2^>^&1') do set PYTHON_VERSION=%%i
echo [OK]  Python version: %PYTHON_VERSION%
echo.

REM Check dependencies
echo [INFO] Checking dependencies...
%PYTHON_EXE% -c "import numpy, pandas, yaml, requests" 2> nul
if errorlevel 1 (
    echo [WARN] Missing dependencies, installing...
    echo [INFO] Using python -m pip to ensure correct target...
    %PYTHON_EXE% -m pip install --upgrade pip 2> nul
    %PYTHON_EXE% -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] Dependency install failed
        echo [INFO] Try manually: %PYTHON_EXE% -m pip install numpy pandas pyarrow pyyaml requests websocket-client
        pause
        exit /b 1
    )
    echo [OK]  Dependencies installed
) else (
    echo [OK]  Dependencies ready
)

REM Check .env
echo.
if not exist .env (
    echo [WARN] .env file not found
    if exist .env.example (
        echo [INFO] Copying .env.example to .env...
        copy .env.example .env > nul
        echo [INFO] Please edit .env with your settings
        notepad .env
    ) else (
        echo [ERROR] .env.example template not found
        echo Please create .env manually
        pause
        exit /b 1
    )
) else (
    echo [OK]  .env file found
)

REM Check config
echo.
if not exist config\base.yaml (
    echo [ERROR] Config not found: config\base.yaml
    pause
    exit /b 1
) else (
    echo [OK]  Config file found
)

REM Launch with auto-restart
echo.
echo [INFO] Starting application...
echo ========================================
echo.

set RESTART_COUNT=0

:start
%PYTHON_EXE% main.py
set EXIT_CODE=%errorlevel%

echo.
echo ========================================
echo  Exit code: %EXIT_CODE%
echo ========================================
echo.

if %EXIT_CODE%==0 (
    echo Normal exit
    goto end
) else (
    set /a RESTART_COUNT+=1
    echo Abnormal exit (attempt #%RESTART_COUNT%)
    
    if %RESTART_COUNT% GEQ 10 (
        echo.
        echo [ERROR] Restarted 10 times, stopping
        echo Check logs for details:
        echo   logs\okx_signal_*.log
        pause
        exit /b 1
    )
    
    echo Restarting in %RESTART_COUNT% seconds...
    timeout /t %RESTART_COUNT% /nobreak > nul
    goto start
)

:end
pause
