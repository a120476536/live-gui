#!/bin/bash
# 重启本地 WebSocket 服务：
#   1) 先检测并杀掉所有残留的 live_server.py 实例
#   2) 用 venv 的 python 重新拉起一个全新实例（脱离终端，避免被 Ctrl+Z 挂起）
# 用法：
#   bash restart_ws.sh                       # room-id / cookie 都从 data/app_config.json 取（GUI 里保存的那份）
#   bash restart_ws.sh 6096197105            # 指定 room-id，cookie 仍取 app_config
#   bash restart_ws.sh 6096197105 data/cookie.txt  # 指定 room-id + cookie 文件
# 注意：必须用 venv 的 python（websockets 装在 venv 里），裸 python 会 ModuleNotFoundError。
# 注意2：live_server.py 的 cookie 是必填，而 nohup 启动没有 stdin（交互式 input() 会卡死），
#        所以这里一律走 --cookie-file；缺失时自动从 data/app_config.json 的 dy_cookie 导出。

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR" || exit 1

# 跨平台 venv python 定位：Windows(Git Bash) 走 Scripts/python.exe，mac/Linux 走 bin/python
if [ -x "./venv/Scripts/python.exe" ]; then
    VENV_PY="./venv/Scripts/python.exe"
elif [ -x "./venv/bin/python" ]; then
    VENV_PY="./venv/bin/python"
else
    echo "    [错误] 找不到 venv python，请先在项目根目录重建 venv 并安装依赖"
    exit 1
fi

# room-id 未显式指定时，取 GUI 保存的 data/app_config.json（dy_room_id）
ROOM_ID="${1:-}"
if [ -z "$ROOM_ID" ]; then
    ROOM_ID="$("$VENV_PY" -c "
import json
try:
    print(json.load(open('data/app_config.json', encoding='utf-8')).get('dy_room_id', '') or '')
except Exception:
    print('')
" 2>/dev/null)"
    ROOM_ID="${ROOM_ID:-340706331114}"
fi

COOKIE_FILE="${2:-data/cookie.txt}"
# cookie 文件不存在时，从 GUI 保存的 app_config.json（dy_cookie）导出，避免交互式输入卡死
if [ ! -s "$COOKIE_FILE" ]; then
    "$VENV_PY" -c "
import json, os
try:
    ck = json.load(open('data/app_config.json', encoding='utf-8')).get('dy_cookie', '') or ''
except Exception:
    ck = ''
if ck:
    os.makedirs(os.path.dirname('$COOKIE_FILE') or '.', exist_ok=True)
    open('$COOKIE_FILE', 'w', encoding='utf-8').write(ck)
    print('    已从 data/app_config.json 导出 cookie -> $COOKIE_FILE (长度 %d)' % len(ck))
"
fi
if [ ! -s "$COOKIE_FILE" ]; then
    echo "    [错误] 缺少 cookie：请把抖音 Cookie 存入 $COOKIE_FILE，或在 GUI 里填写一次（会写入 data/app_config.json）"
    exit 1
fi

WS_HOST="0.0.0.0"        # 监听所有网卡：手机/局域网设备访问 receipt 页时也能连上（只本机用可改回 127.0.0.1）
WS_PORT="8765"
GUI_OFFLINE_EXIT="0"     # GUI(liveMain) 关掉后多久自动停 live_server：0=立即停（默认）；填秒数=延迟停；填 -1=永不自动停
LOG="/tmp/live_server.log"

echo "==> 工作目录: $DIR"
echo "==> 目标 room-id: $ROOM_ID"
echo "==> Cookie 文件: $COOKIE_FILE"

# 1) 检测并杀掉所有残留的 live_server.py 实例
echo "==> [1/3] 检测残留 live_server.py 进程 ..."
OLD_PIDS="$(pgrep -f 'python.*live_server\.py' || true)"
if [ -n "$OLD_PIDS" ]; then
    echo "    发现残留 PID: $OLD_PIDS ，正在终止 ..."
    pkill -9 -f 'python.*live_server\.py' 2>/dev/null || true
    sleep 1
    REMAIN="$(pgrep -f 'python.*live_server\.py' || true)"
    if [ -n "$REMAIN" ]; then
        echo "    [警告] 仍有进程未退出: $REMAIN"
    else
        echo "    已清理干净"
    fi
else
    echo "    无残留进程"
fi

# 端口占用兜底：直接杀掉占用 8765 的进程（无论以何种方式启动，确保端口一定释放）
LISTEN_PID="$(lsof -tiTCP:"$WS_PORT" -sTCP:LISTEN 2>/dev/null || true)"
if [ -n "$LISTEN_PID" ]; then
    echo "    端口 $WS_PORT 仍被 PID $LISTEN_PID 占用，强制释放 ..."
    kill -9 $LISTEN_PID 2>/dev/null || true
    sleep 1
fi

# 2) 用 venv python 重新启动
echo "==> [2/3] 用 $VENV_PY 启动新实例 ..."
if [ ! -x "$VENV_PY" ]; then
    echo "    [错误] 找不到 venv python: $VENV_PY"
    echo "    请确认 live-gui 下已创建 venv 并 pip install websockets"
    exit 1
fi

# 用 venv python 做一层「脱离会话」的启动器：
#   单纯 nohup ... & 仍属于当前终端的进程组，终端/会话一收摊就会被一起回收
#   （表现：端口显示 LISTEN，但握手挂死或直接 Connection refused）。
#   start_new_session(mac/Linux) / CREATE_NEW_PROCESS_GROUP(Windows) 让服务真正常驻。
"$VENV_PY" - "$ROOM_ID" "$COOKIE_FILE" "$WS_HOST" "$WS_PORT" "$LOG" "$GUI_OFFLINE_EXIT" <<'PYEOF' &
import os, subprocess, sys
room_id, cookie_file, ws_host, ws_port, log_path, gui_offline_exit = sys.argv[1:7]
log = open(log_path, "ab", buffering=0)
kw = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
p = subprocess.Popen(
    [sys.executable, "live_server.py", "--room-id", room_id, "--cookie-file", cookie_file,
     "--ws-host", ws_host, "--ws-port", ws_port,
     "--gui-offline-exit", gui_offline_exit],
    stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, **kw
)
print("    已脱离会话启动 (PID %d)" % p.pid)
PYEOF
sleep 1
echo "    启动命令已派发（日志: $LOG）"

# 3) 等待并验证
echo "==> [3/3] 等待 4s 后验证 ..."
sleep 4
NEW_PID="$(pgrep -f 'python.*live_server\.py' | head -n1 || true)"
if [ -n "$NEW_PID" ]; then
    echo "    新进程 PID=$NEW_PID"
else
    echo "    [错误] 未检测到新进程，启动可能失败，查看日志:"
    tail -n 20 "$LOG"
    exit 1
fi

if lsof -nP -iTCP:"$WS_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "    端口 $WS_PORT 监听正常 ✓"
else
    echo "    [错误] 端口 $WS_PORT 未监听，查看日志:"
    tail -n 20 "$LOG"
    exit 1
fi

# 端口在听 ≠ 能握手：真连一次 WS 才算通（旧实例占坑时端口是听的但握手会挂死）
HANDSHAKE="$("$VENV_PY" - <<'PYEOF' 2>/dev/null
import asyncio, websockets
async def main():
    try:
        async with websockets.connect("ws://127.0.0.1:8765", open_timeout=5) as ws:
            try:
                await asyncio.wait_for(ws.recv(), timeout=8)
                return "OK(已收到数据)"
            except asyncio.TimeoutError:
                return "OK(握手成功,8s内无事件)"
    except Exception as e:
        return "FAIL: %s: %s" % (type(e).__name__, e)
print(asyncio.run(main()))
PYEOF
)"
echo "    WS 握手验证: $HANDSHAKE"
case "$HANDSHAKE" in
    OK*) ;;
    *) echo "    [错误] 握手失败，查看日志:"; tail -n 20 "$LOG"; exit 1;;
esac

echo "==> 完成。日志: tail -f $LOG"
echo "    浏览器打开 http://localhost:8790/live/birthday 即可（http 同源才能用明文 ws://localhost:$WS_PORT）"
