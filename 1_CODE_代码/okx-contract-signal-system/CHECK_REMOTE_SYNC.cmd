@echo off
setlocal
cd /d "%~dp0"
py -3.12 scripts\check_change_governance.py --require-github-sync
exit /b %errorlevel%
