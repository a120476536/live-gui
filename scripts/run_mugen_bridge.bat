@echo off
REM ===========================================================================
REM run_mugen_bridge.bat  - 仅启动弹幕桥（MUGEN 需另行启动）
REM 前置：vJoy 驱动已装、live-gui WS 8765 在听、MUGEN 已运行。
REM ===========================================================================
set "ROOT=%~dp0.."
cd /d "%ROOT%"

set "http_proxy="
set "https_proxy="
set "all_proxy="
set "HTTP_PROXY="
set "HTTPS_PROXY="
set "ALL_PROXY="

set "PY="
if exist "%LocalAppData%\Programs\Python\Python312\python.exe" set "PY=%LocalAppData%\Programs\Python\Python312\python.exe"
if not defined PY for %%P in (python.exe) do ( if not defined PY set "PY=%%~$PATH:P" )
if not defined PY (
    echo [ERROR] 未找到 Python312
    pause
    exit /b 1
)

echo [START] bridge -> ws://127.0.0.1:8765  (日志 mugen_bridge.log)
start "" /min cmd /c ""%PY%" "%ROOT%\utils\mugen_bridge.py" > "%ROOT%\mugen_bridge.log" 2>&1"
echo [OK] bridge 已后台启动。
exit /b
