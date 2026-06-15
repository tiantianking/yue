@echo off
setlocal EnableExtensions EnableDelayedExpansion
title OKX Signal System v3.34

cd /d "%~dp0"

echo ========================================
echo  OKX Signal System v3.34
echo ========================================
echo.

set "PYTHON_EXE="
set "LOCAL_PY=%~dp0..\..\LOCAL_DEPS\venv\Scripts\python.exe"

if exist "%LOCAL_PY%" (
    "%LOCAL_PY%" --version > nul 2>&1
    if errorlevel 1 (
        echo [WARN] Workspace Python exists but cannot run; trying system Python.
        goto try_py_launcher
    )
    set "PYTHON_EXE=%LOCAL_PY%"
    echo [INFO] Using workspace Python
    goto python_found
)

:try_py_launcher
py -3 --version > nul 2>&1
if not errorlevel 1 (
    set "PYTHON_EXE=py -3"
    echo [INFO] Using Python launcher
    goto python_found
)

python --version > nul 2>&1
if not errorlevel 1 (
    set "PYTHON_EXE=python"
    echo [INFO] Using Python from PATH
    goto python_found
)

echo [ERROR] Python 3.11+ was not found.
echo Install Python or restore LOCAL_DEPS\venv, then run this launcher again.
pause
exit /b 1

:python_found
%PYTHON_EXE% --version
echo.

if not defined OKX_IS_SIMULATED set "OKX_IS_SIMULATED=true"

echo [INFO] Checking dependencies...
%PYTHON_EXE% -c "import numpy, pandas, yaml, requests, pyarrow, websocket" > nul 2>&1
if errorlevel 1 (
    echo [WARN] Missing dependencies; installing from requirements.
    set "REQ_FILE=requirements.txt"
    if not exist "!REQ_FILE!" set "REQ_FILE=requirements.lock"
    if not exist "!REQ_FILE!" (
        echo [ERROR] requirements.txt or requirements.lock not found.
        pause
        exit /b 1
    )
    %PYTHON_EXE% -m pip install -r "!REQ_FILE!"
    if errorlevel 1 (
        echo [ERROR] Dependency install failed.
        pause
        exit /b 1
    )
)
echo [OK] Dependencies ready
echo.

if not exist "config\base.yaml" (
    echo [ERROR] Missing config\base.yaml
    pause
    exit /b 1
)

if not exist ".env" (
    echo [INFO] .env not found; simulated mode is used unless environment variables are set.
)

echo [INFO] Starting desktop app...
echo.
%PYTHON_EXE% main.py --auto-start
set "EXIT_CODE=%errorlevel%"

echo.
echo Exit code: %EXIT_CODE%
if not "%EXIT_CODE%"=="0" (
    echo [ERROR] App exited with an error. Check logs for details.
)
pause
exit /b %EXIT_CODE%
