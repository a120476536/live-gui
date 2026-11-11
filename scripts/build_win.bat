@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
REM ============================================================
REM Windows 打包脚本 v2（与 build_mac.sh 对齐：全程错误检查）
REM   - 不能用 PyInstaller 交叉编译，必须在 Windows 本机运行
REM   - 默认使用项目 venv；没有则使用 build_env
REM   - 自动定位并校验 py_mini_racer 的二进制/数据文件，不再硬编码
REM   - 每一步都检查 errorlevel，任何一步失败立即停止并报错
REM     （等价于 build_mac.sh 顶部的 set -e）
REM   - 打包前先结束旧进程，避免文件被占用
REM   - 否则 COLLECT 阶段产物会残缺
REM 用法：在项目根目录双击本文件，或执行 build_win.bat
REM 规格：本文件必须保存为 UTF-8 with BOM，以配合上面的 chcp 65001
REM ============================================================
cd /d "%~dp0.."

REM ---------- 1) 选择虚拟环境 ----------
set "VENV_PY="
if exist "venv\Scripts\python.exe" (
  set "VENV_PY=venv\Scripts\python.exe"
) else if exist "build_env\Scripts\python.exe" (
  set "VENV_PY=build_env\Scripts\python.exe"
)
if not defined VENV_PY (
  echo [错误] 未找到 venv 或 build_env，请先创建虚拟环境并安装依赖：
  echo         python -m venv venv
  echo         venv\Scripts\activate
  echo         pip install -r requirements.txt pyinstaller
  goto :fail
)
for %%i in ("%VENV_PY%") do set "VENV_PY=%%~fi"
echo [1/7] 虚拟环境: %VENV_PY%

REM ---------- 2) 确保 PyInstaller 可用，并结束旧进程 ----------
%VENV_PY% -c "import PyInstaller" >nul 2>&1
if errorlevel 1 (
  echo [2/7] 未检测到 PyInstaller，正在安装...
  %VENV_PY% -m pip install --disable-pip-version-check pyinstaller
  if errorlevel 1 (
    echo [错误] PyInstaller 安装失败，请检查网络后手动执行：
    echo         %VENV_PY% -m pip install pyinstaller
    goto :fail
  )
) else (
  echo [2/7] PyInstaller 已安装，跳过安装
)
taskkill /IM liveMain.exe /F >nul 2>&1
taskkill /IM updater.exe /F >nul 2>&1
REM 轮询等待进程真正退出（最多 15 秒），而不是死等固定 2 秒
set "WAITN=0"
:wait_proc_exit
tasklist /FI "IMAGENAME eq liveMain.exe" 2>nul | findstr /I /C:"liveMain.exe" >nul
if not errorlevel 1 (
  set /a WAITN+=1
  if !WAITN! lss 15 (
    ping -n 2 127.0.0.1 >nul
    goto :wait_proc_exit
  )
  echo [错误] liveMain.exe 仍在运行，无法继续打包，请手动结束该进程后重试
  goto :fail
)
echo       已结束可能在运行的 liveMain.exe / updater.exe
REM 清理上一次产物：避免残留的 d3dcompiler_47.dll 等被占用导致 COLLECT 复制失败
REM 注意：build 缓存交由 PyInstaller 的 --clean 处理，这里不主动删 build，
REM       因为 360 主动防御会对“删除含 exe/dll 的目录”弹窗拦截，导致脚本卡死
if exist "dist\liveMain" ( rmdir /s /q "dist\liveMain" 2>nul )
if exist "dist\updater.exe" ( del /f /q "dist\updater.exe" 2>nul )
echo       已清理 dist\liveMain 旧产物

REM ---------- 3) 定位并校验 py_mini_racer ----------
set "PYMR_DIR="
for /f "usebackq delims=" %%i in (`%VENV_PY% -c "import os,py_mini_racer;print(os.path.dirname(py_mini_racer.__file__))"`) do set "PYMR_DIR=%%i"
if not defined PYMR_DIR (
  echo [错误] 无法定位 py_mini_racer，请先安装：
  echo         %VENV_PY% -m pip install mini_racer
  goto :fail
)
set "MISSING="
for %%f in (mini_racer.dll icudtl.dat snapshot_blob.bin) do (
  if not exist "%PYMR_DIR%\%%f" set "MISSING=!MISSING! %%f"
)
if defined MISSING (
  echo [错误] py_mini_racer 目录缺少必需文件:!MISSING!
  echo        目录: %PYMR_DIR%
  goto :fail
)
echo [3/7] py_mini_racer 目录: %PYMR_DIR%

REM ---------- 4) 打包主程序 ----------
REM  --add-data / --add-binary 在 Windows 用分号 ';' 分隔（macOS 用冒号 ':'）
REM  sign.js / a_bogus.js / ac_signature.py / protobuf 是运行时动态读取的文件，必须带进去
REM  本版本 PyInstaller（one-folder）会把 --add-data 统一放到 _internal 下：
REM    - js/sign.js        -> dist/liveMain/_internal/js/sign.js
REM    - js/a_bogus.js     -> dist/liveMain/_internal/js/a_bogus.js
REM    - utils/ac_signature.py -> dist/liveMain/_internal/utils/ac_signature.py
REM    - protobuf          -> dist/liveMain/_internal/protobuf
REM  运行时一律用 server/WebSocketServerD.py 的 _resource_path() 解析（先查 _internal，
REM  再查 _MEIPASS[one-file]，最后回退项目根[dev]），切勿写死 base_dir/js/... 否则打包后找不到。
REM  py_mini_racer 的 dll/dat/bin 经 --add-binary 落到 _internal 根目录（正是其查找位置）。
REM  requests 是 Updater.py（liveMain 直接 import）的依赖，必须 hidden-import
echo [4/7] 打包主程序 liveMain，耗时较长请耐心等待...
REM 失败自动重试：360 主动防御 / 杀软实时防护会偶发拒绝写入 Qt 的 dll
REM （表现为 COLLECT 阶段 PermissionError: [Errno 13] ... MSVCP140.dll）
REM 重试逻辑放在纯 ASCII 的 _build_retry.bat 里，避免中文批处理的解析问题
set "BUILD_MAX_TRY=3"
call "%~dp0_build_retry.bat" "%VENV_PY%" -m PyInstaller -w ^
  --noconfirm --clean ^
  --name "liveMain" ^
  --icon "favicon.ico" ^
  --add-data "js/sign.js;js" ^
  --add-data "js/a_bogus.js;js" ^
  --add-data "utils/ac_signature.py;utils" ^
  --add-data "scripts/restart_ws.sh;scripts" ^
  --add-data "scripts/kill_8765.sh;scripts" ^
  --add-data "scripts/kill_8765.ps1;scripts" ^
  --add-data "protobuf;protobuf" ^
  --add-binary "%PYMR_DIR%\mini_racer.dll;." ^
  --add-binary "%PYMR_DIR%\icudtl.dat;." ^
  --add-binary "%PYMR_DIR%\snapshot_blob.bin;." ^
  --hidden-import execjs ^
  --hidden-import requests ^
  --hidden-import protobuf ^
  --hidden-import protobuf.douyin ^
  --hidden-import usb.core ^
  --hidden-import usb.backend.libusb1 ^
  --hidden-import server.live_server ^
  page/liveMain.py
if errorlevel 1 (
  echo [错误] 主程序打包失败，已自动重试 3 次仍未成功
  echo        若报错是 PermissionError 或 Errno 13 且指向某个 dll，按下面顺序处理：
  echo        1. 退出 360 安全卫士等安全软件，再重跑本脚本
  echo        2. 或把项目目录 D:\mayun\live-gui 加入其信任区白名单
  echo        3. 关闭占用句柄的程序，例如运行中的 liveMain.exe 或 Everything
  goto :fail
)

REM 产物完整性校验：专门挡住“脚本报成功、dist 其实残缺”这种情况
if not exist "dist\liveMain\liveMain.exe" (
  echo [错误] 未生成 dist\liveMain\liveMain.exe
  goto :fail
)
for %%f in (js\sign.js js\a_bogus.js utils\ac_signature.py mini_racer.dll icudtl.dat snapshot_blob.bin) do (
  if not exist "dist\liveMain\_internal\%%f" (
    echo [错误] 产物不完整，缺少 dist\liveMain\_internal\%%f
    goto :fail
  )
)
echo       产物校验通过：dist\liveMain\liveMain.exe

REM ---------- 5) 打包更新程序 updater ----------
echo [5/7] 打包更新程序 updater ...
%VENV_PY% -m PyInstaller -w ^
  --noconfirm --clean ^
  --onefile ^
  --name "updater" ^
  --icon "favicon.ico" ^
  --hidden-import requests ^
  utils/updater_worker.py
if errorlevel 1 (
  echo [错误] updater 打包失败
  goto :fail
)
if not exist "dist\updater.exe" (
  echo [错误] 未生成 dist\updater.exe
  goto :fail
)
echo       产物校验通过：dist\updater.exe

REM ---------- 6) 把 updater.exe 放进 liveMain 同目录 ----------
copy /Y "dist\updater.exe" "dist\liveMain\updater.exe" >nul
if errorlevel 1 (
  echo [错误] 复制 updater.exe 到 dist\liveMain 失败
  goto :fail
)
if not exist "dist\liveMain\updater.exe" (
  echo [错误] dist\liveMain\updater.exe 复制后仍不存在
  goto :fail
)
echo [6/7] 已复制 updater.exe 到 dist\liveMain\updater.exe

REM ---------- 7) 完成 ----------
echo [7/7] ==================================================
echo 打包完成，双击 dist\liveMain\liveMain.exe 运行
echo 注意：a_bogus 计算依赖 execjs，运行机器需安装 Node.js 并加入 PATH
echo 注意：mini_racer.dll / icudtl.dat / snapshot_blob.bin 已放在 _internal 根目录，
echo       这正是 py_mini_racer 在 PyInstaller 环境下查找的位置，无需再调整
echo 更新程序 updater.exe 已一并生成并放入 liveMain 同目录（自动更新可用）
echo ==================================================
endlocal
pause
exit /b 0

:fail
echo.
echo ==================================================
echo 打包中止：上面某一步失败了，dist 里的产物不完整，请勿直接分发。
echo ==================================================
endlocal
pause
exit /b 1
