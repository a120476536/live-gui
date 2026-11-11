#!/bin/bash
# kill_8765.sh - 杀掉本机指定端口上的监听 / 连接进程
# Windows(Git Bash) / WSL / macOS / Linux 通用
#
# 用法：
#   bash kill_8765.sh                # 默认 8765，只杀 LISTEN（服务端）
#   bash kill_8765.sh --all          # 8765 上服务端 + 客户端连接都杀
#   bash kill_8765.sh 8080           # 指定其他端口
#   bash kill_8765.sh 8080 --all     # 指定端口 + 全部杀
#   bash kill_8765.sh --all -y       # 跳过确认
#
# 说明：macOS/Linux 走 lsof（netstat 在 macOS 上既没有 -o 也没有 PID 列，
#       老版本脚本在那里会扫出空结果、误报“端口干净”却什么都没杀）；
#       Windows 走 netstat -ano + taskkill。

PORT=8765
KILL_ALL=0
ASSUME_YES=0

# 参数解析
while [ $# -gt 0 ]; do
    case "$1" in
        --all|-a) KILL_ALL=1 ;;
        --yes|-y) ASSUME_YES=1 ;;
        -h|--help)
            sed -n '2,14p' "$0"
            exit 0 ;;
        [0-9]*)   PORT="$1" ;;
        *) echo "未知参数: $1"; exit 1 ;;
    esac
    shift
done

# 平台判定
IS_WIN=0
case "$(uname -s 2>/dev/null)" in
    MINGW*|MSYS*|CYGWIN*|Windows_NT) IS_WIN=1 ;;
esac

echo ""
echo "=== 端口清理脚本 (port=$PORT, all=$KILL_ALL, win=$IS_WIN) ==="
echo ""

# 1) 找出占用该端口的 PID（TIME_WAIT 是内核态残留，没有进程，自动忽略）
collect_pids() {
    if [ "$IS_WIN" = "1" ]; then
        local lines
        lines=$(netstat -ano 2>/dev/null | grep ":$PORT " | grep -v "TIME_WAIT")
        if [ "$KILL_ALL" = "1" ]; then
            echo "$lines" | awk 'NF {print $NF}'
        else
            echo "$lines" | awk '$NF=="LISTENING" {print $(NF-0)}'
        fi
    else
        if command -v lsof >/dev/null 2>&1; then
            if [ "$KILL_ALL" = "1" ]; then
                lsof -nP -iTCP:"$PORT" -t 2>/dev/null
            else
                lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null
            fi
        elif command -v fuser >/dev/null 2>&1; then
            fuser -n tcp "$PORT" 2>/dev/null | tr -s ' ' '\n'
        fi
    fi | grep -E '^[0-9]+$' | sort -un
}

PIDS_TO_KILL=$(collect_pids)

if [ -z "$PIDS_TO_KILL" ]; then
    echo "[OK] 端口 $PORT 上没有需要清理的进程"
    echo ""
    exit 0
fi

# 2) 展示将杀掉的进程
echo "[目标进程]"
for pid in $PIDS_TO_KILL; do
    PROC=$(ps -p "$pid" -o comm= 2>/dev/null || echo "<已退出>")
    echo "  PID=$pid  进程=$PROC"
done

# 3) 确认
echo ""
echo "=== 准备杀 PID: $(echo $PIDS_TO_KILL | tr '\n' ' ') ==="
if [ "$ASSUME_YES" != "1" ]; then
    read -p "确认？(y/N) " confirm
    case "$confirm" in
        y|Y) ;;
        *) echo "已取消"; echo ""; exit 0 ;;
    esac
fi

# 4) 杀进程
echo ""
echo "=== 开始杀进程 ==="
for pid in $PIDS_TO_KILL; do
    if [ "$IS_WIN" = "1" ]; then
        # Windows 上 taskkill 比 kill -9 干净（不会留下僵尸子进程）
        taskkill //F //PID "$pid" 2>&1 | sed 's/^/  /'
    else
        if kill -9 "$pid" 2>/dev/null; then
            echo "  [OK] kill -9 $pid"
        else
            echo "  [FAIL] kill -9 $pid 失败（权限不足或进程已退出）"
        fi
    fi
done

# 5) 验证
sleep 1
echo ""
echo "=== 杀完后端口状态 ==="
REMAIN=$(collect_pids)
if [ -z "$REMAIN" ]; then
    echo "[OK] 端口 $PORT 已清空"
else
    echo "[WARN] 还有残留："
    for pid in $REMAIN; do
        PROC=$(ps -p "$pid" -o comm= 2>/dev/null || echo "<已退出>")
        echo "  PID=$pid  进程=$PROC"
    done
fi
echo ""
