@echo off
chcp 65001 > nul
setlocal EnableExtensions
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"

set "PYTHON_EXE=%~dp0..\..\LOCAL_DEPS\venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
    echo [ERROR] Workspace Python not found: %PYTHON_EXE%
    exit /b 1
)

if not exist "logs" mkdir "logs"

"%PYTHON_EXE%" scripts\run_candidate_factory.py >> "logs\parallel_acceptance.log" 2>&1
set "FACTORY_EXIT=%errorlevel%"
if not "%FACTORY_EXIT%"=="0" (
    echo [ERROR] Candidate factory failed. See logs\parallel_acceptance.log
    exit /b %FACTORY_EXIT%
)

"%PYTHON_EXE%" scripts\run_parallel_acceptance.py >> "logs\parallel_acceptance.log" 2>&1
set "EXIT_CODE=%errorlevel%"
if not "%EXIT_CODE%"=="0" (
    echo [ERROR] Parallel acceptance failed. See logs\parallel_acceptance.log
    exit /b %EXIT_CODE%
)

echo [OK] Candidate factory and research shadows updated.
echo      Factory: outputs\candidate_factory_status.json
echo      Acceptance: outputs\parallel_acceptance_status.json
exit /b 0
