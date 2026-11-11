#!/usr/bin/env bash
# ============================================================
# macOS 打包脚本（参考 README 中已验证的 Win 指令改写）
#   - 不能用 PyInstaller 交叉编译，必须在 macOS 本机运行
#   - 默认使用项目自带 venv（python3.12）；没有则自建 build_env
#   - 自动定位 py_mini_racer 的二进制/数据文件，无需硬编码路径
# 用法：
#   chmod +x build_mac.sh
#   ./build_mac.sh
# ============================================================
set -e

# 切换到脚本所在目录（项目根）
cd "$(cd "$(dirname "$0")" && pwd)"

# 1) 选择虚拟环境（优先 venv，其次 build_env）
if [ -x "./venv/bin/python3" ]; then
  VENV_PY="./venv/bin/python3"
elif [ -x "./build_env/bin/python3" ]; then
  VENV_PY="./build_env/bin/python3"
else
  echo "未找到 venv/build_env，请先创建虚拟环境并安装依赖："
  echo "  python3 -m venv venv && source venv/bin/activate"
  echo "  pip install -r requirements.txt pyinstaller"
  exit 1
fi

# 2) 确保 pyinstaller 已安装
"$VENV_PY" -m pip install -q pyinstaller || true

# 3) 定位 py_mini_racer 目录（WebSocketServerD 用它计算抖音签名/a_bogus）
PYMR_DIR=$("$VENV_PY" -c "import os, py_mini_racer; print(os.path.dirname(py_mini_racer.__file__))")
echo "py_mini_racer 目录: $PYMR_DIR"

# 4) 收集 py_mini_racer 所需的二进制/数据文件（mac 为 libmini_racer.dylib + 两个 dat）
EXTRA_BIN=()
for cand in libmini_racer.dylib mini_racer.dylib mini_racer.so; do
  if [ -f "$PYMR_DIR/$cand" ]; then
    EXTRA_BIN+=(--add-binary "$PYMR_DIR/$cand:.")
    break
  fi
done
for f in icudtl.dat snapshot_blob.bin; do
  if [ -f "$PYMR_DIR/$f" ]; then
    EXTRA_BIN+=(--add-binary "$PYMR_DIR/$f:.")
  fi
done

# 5) 打包主程序
#    --add-data 在 macOS 用冒号 ':' 分隔（Win 用分号 ';'）
#    sign.js / a_bogus.js / ac_signature.py 是运行时动态读取的 JS/py，必须带进去
#    execjs 在 get_a_bogus 里被 import，且运行机器需装 Node.js（否则 a_bogus 计算失败）
#    requests 是 Updater.py（liveMain 直接 import）的依赖，必须 hidden-import
#    restart_ws.sh / kill_8765.sh / kill_8765.ps1 是更新/端口清理辅助脚本，一并带进包
"$VENV_PY" -m PyInstaller -w \
  --noconfirm --clean \
  --name "liveMain" \
  --icon "favicon.ico" \
  --add-data "js/sign.js:js" \
  --add-data "js/a_bogus.js:js" \
  --add-data "utils/ac_signature.py:utils" \
  --add-data "scripts/restart_ws.sh:scripts" \
  --add-data "scripts/kill_8765.sh:scripts" \
  --add-data "scripts/kill_8765.ps1:scripts" \
  --add-data "protobuf:protobuf" \
  --hidden-import execjs \
  --hidden-import requests \
  --hidden-import protobuf \
  --hidden-import protobuf.douyin \
  --hidden-import usb.core \
  --hidden-import usb.backend.libusb1 \
  "${EXTRA_BIN[@]}" \
  page/liveMain.py

# 6) 打包更新程序 updater（独立可执行文件，更新时由 liveMain 通过 QProcess 拉起）
#    用 --onefile 生成单文件，方便直接塞进 liveMain.app 同目录
"$VENV_PY" -m PyInstaller -w \
  --noconfirm --clean \
  --onefile \
  --name "updater" \
  --icon "favicon.ico" \
  --hidden-import requests \
  utils/updater_worker.py

# 7) 把 updater 放进 liveMain.app/Contents/MacOS/（liveMain 更新时会找同目录的 updater）
if [ -f "dist/updater" ]; then
  cp "dist/updater" "dist/liveMain.app/Contents/MacOS/updater.exe"
  echo "已复制 updater -> dist/liveMain.app/Contents/MacOS/updater.exe"
else
  echo "[警告] 未生成 dist/updater，自动更新将不可用"
fi

echo "=================================================="
echo "打包完成，双击 dist/liveMain.app 运行"
echo "注意：a_bogus 计算依赖 execjs -> 运行机器需安装 Node.js 并加入 PATH"
echo "若启动报 mini_racer 缺失，把上面 --add-binary 的目标 '.' 改为 'py_mini_racer' 再打包"
echo "更新程序 updater 已一并生成并放入 app 同目录（Windows 自动更新可用）"
echo "=================================================="
