@echo off
REM ===========================================================================
REM start_mugen_danmaku.bat  - one-click: launch MUGEN VS match (八神庵 AI 自动出招)
REM
REM 免驱动方案（B 路线）：八神庵(Yagami/P2) 用 MUGEN 内置 AI 自动连招/放必杀，
REM 不需要键盘注入、不需要 vJoy、不需要焦点。角色自带 AI（Yagami-3.cns），
REM 只要启动时给 P2 加 -p2.ai <等级> 即激活（AILevel>0 -> var(59)=3+AILevel）。
REM
REM 双击本文件即可（普通权限，无需管理员）。
REM 前置：live-gui 主程序(liveMain.py)已运行、WS 8765 在听；或单独用
REM       python utils/danmaku_mock.py --demo 做测试。
REM
REM 注意：MUGEN 1.1 没有"外部事件->角色内部"的通道，所以礼物/弹幕不能精确指定
REM 某一招（只能让角色按战况自动出招）。要"每条礼物=固定招式"仍需 vJoy 手柄方案。
REM ===========================================================================

set "MUGEN=D:\mayun\mugen"
set "LIVEGUI=D:\mayun\live-gui"
set "P1=iori"
set "P2=Yagami"
set "AILEV=8"

REM 清系统代理，确保 websockets 直连 127.0.0.1:8765（VPN 修复）
set "http_proxy="
set "https_proxy="
set "all_proxy="
set "HTTP_PROXY="
set "HTTPS_PROXY="
set "ALL_PROXY="

REM 干净重启：杀掉上次的 MUGEN（不杀 python，保留 live-gui）
taskkill /f /im mugen.exe >nul 2>&1

REM ---- 1) 启动 MUGEN 直接进对战（跳过选人）----
cd /d "%MUGEN%"
if not exist "%MUGEN%\mugen.exe" (
    echo [ERROR] mugen.exe not found at %MUGEN%\mugen.exe
    pause
    exit /b 1
)
start "" "%MUGEN%\mugen.exe" -p1 %P1% -p2 %P2% -p2.ai %AILEV% -rounds 99

REM ---- 2) 启动弹幕桥（后台，日志 mugen_bridge.log）----
cd /d "%LIVEGUI%"
set "PY="
if exist "%LocalAppData%\Programs\Python\Python312\python.exe" set "PY=%LocalAppData%\Programs\Python\Python312\python.exe"
if not defined PY for %%P in (python.exe) do ( if not defined PY set "PY=%%~$PATH:P" )
if not defined PY (
    echo [ERROR] 未找到 Python，请确认已安装 Python312 且含 websockets。
    pause
    exit /b 1
)
echo [START] MUGEN (八神庵 AI=%AILEV%) + bridge(礼物记录)。日志见 %LIVEGUI%\mugen_bridge.log
start "" /min cmd /c ""%PY%" "%LIVEGUI%\utils\mugen_bridge.py" > "%LIVEGUI%\mugen_bridge.log" 2>&1"

echo [OK] 已启动。八神庵(P2)将自动连招/放必杀（AI 模式，免驱动）。
echo      手动 WASD/方向键可操控草薙(P1)；若想双方都自动，把启动命令加 -p1.ai %AILEV%。
echo      礼物/弹幕会记录在 mugen_bridge.log（不精确驱动招式，属 AI 模式预期）。
timeout /t 2 >nul
exit
