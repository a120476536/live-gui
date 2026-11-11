import os, sys
# 让项目根目录进入 sys.path，保证以 `python page/liveMain.py` 方式运行时
# 仍能解析 `from server.xxx / core.xxx / utils.xxx / widget.xxx` 等包导入。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 中文 Windows 控制台默认编码为 gbk，而 SerialManager / WebSocketServerD 等模块
# 有大量 emoji（✅🔄📤⚠️❌…）调试输出。这些字符不在 gbk 字符集，一旦 print 到
# 控制台就会抛 UnicodeEncodeError，且发生在「模块导入期」（导入
# SerialManager -> 模块级实例化 -> __init__ 里的 print），会直接让整个 GUI 闪退。
# 另外：本程序以 PyInstaller -w（窗口化）打包，sys.stdout/stderr 为 None，而标准库
# http.server 的 log_message 会调用 sys.stderr.write(...)，标准 print 也会——None 上
# 调用 .write 会抛 AttributeError，导致 8766 的 /live-config 等每次请求都报错、语音被
# 误判为离线静音。
# 因此在一切业务 import 之前统一处理 stdout/stderr：
#   - 存在且可 reconfigure：强制 utf-8（errors='replace' 兜底）
#   - 为 None（窗口化）：用直接写文件的占位流兜底，避免崩溃并保留调试输出
# 日志文件本身用 utf-8 写不受影响。
if getattr(sys, "frozen", False):
    _BASE_DIR = os.path.dirname(sys.executable)
else:
    _BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

class _StreamToFile:
    """窗口化程序无控制台时，把 stdout/stderr 直接写文件（不用 logging，避免递归）。"""
    def __init__(self, path):
        self._path = path
        self._buf = ""
        self._fh = None
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            self._fh = open(path, "a", encoding="utf-8", buffering=1)
        except Exception:
            self._fh = None
    def write(self, s):
        if not s:
            return
        # 同时落盘，便于窗口化程序排查（无控制台时这是唯一可见的输出）
        if self._fh is not None:
            try:
                self._fh.write(s)
            except Exception:
                pass
    def flush(self):
        if self._fh is not None:
            try:
                self._fh.flush()
            except Exception:
                pass

def _ensure_utf8_streams():
    for _name in ("stdout", "stderr"):
        _stream = getattr(sys, _name, None)
        if _stream is None:
            # 窗口化（PyInstaller -w）下为 None：重定向到文件，避免 .write 崩溃
            _kind = "out" if _name == "stdout" else "err"
            setattr(
                sys, _name,
                _StreamToFile(os.path.join(_BASE_DIR, "log", f"std_{_kind}.log"))
            )
            continue
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            _buf = getattr(_stream, "buffer", None)
            if _buf is not None:
                import io
                setattr(
                    sys, _name,
                    io.TextIOWrapper(
                        _buf, encoding="utf-8", errors="replace",
                        line_buffering=True
                    ),
                )
_ensure_utf8_streams()
# 再加固一层：把已创建的 logging StreamHandler 重定向到安全流，并预占 root handler，
# 防止后续业务模块在「导入期」调用 logging.basicConfig() 时又绑一个 GBK 的 stderr
# （写 emoji 昵称/心跳日志时会刷「--- Logging error ---」traceback）。
from utils.console_safe import install_safe_console
install_safe_console()
import traceback, threading
from datetime import datetime
import ast
import json
import re
from PyQt5.QtGui import QTextCursor, QImage, QTextImageFormat,QPixmap
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QMetaObject, pyqtSlot, QObject,QTimer
from PyQt5.QtWidgets import (QApplication, QWidget, QLabel, QPushButton,
                            QCheckBox,
                            QLineEdit,
                            QComboBox,
                            QDialog,
                            QTextEdit, QVBoxLayout, QHBoxLayout, QMessageBox,
                            QScrollArea)
from PyQt5.QtNetwork import QNetworkAccessManager, QNetworkRequest
import logging
from logging.handlers import TimedRotatingFileHandler
import time
import os, json,tempfile,requests
import subprocess
import platform
from datetime import datetime, timedelta
import serial.tools.list_ports  # 获取串口列表
from io import BytesIO
from utils.Updater import Updater
import uuid
from widget.LockSettingsWidget import LockSettingsWidget   # 顶部
from utils.SerialManager import serial_manager
from server.DyHttpServer import DyHttpServer
from server.live_server import LiveFetcherService, LiveWebSocketServer, WS_PORT
from core.tts_manager import TTSManager
from core.printer_manager import print_mgr
from core.app_state import AppState
from page.receipt_simulator import ReceiptSimulator   # 小票模拟打印页（参照视频玩法）

import importlib.metadata as meta
from utils.thread_utils import run_in_thread

# 获取当前程序目录
if getattr(sys, 'frozen', False):
    # 打包后的可执行文件运行
    base_dir = os.path.dirname(sys.executable)
else:
    # 开发环境中运行：项目根目录（data/ venv/ live_server.py 都在根，而非 page/）
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    
# 获取串口列表 start 
def get_available_ports():
    """获取所有可用的串口列表"""
    ports = []
    try:
        for port in serial.tools.list_ports.comports():
            ports.append(f"{port.device} - {port.description}")
    except Exception as e:
        logger.error(f"liveMain 获取串口列表失败: {str(e)}")
    return ports
# 获取串口列表 end

# 激活码存取 相关 start 
# ACTIVATE_FILE = os.path.join(os.path.dirname(__file__), "activated.json")   # 就在程序同级目录
# ACTIVATE_FILE = os.path.join(os.path.dirname(sys.executable), "activated.json")   # 就在程序同级目录
# if getattr(sys, 'frozen', False):
#     # 打包后的可执行文件运行
#     ACTIVATE_FILE = os.path.join(os.path.dirname(sys.executable), "activated.json")   # 就在程序同级目录
# else:
#     # 开发环境中运行
#     ACTIVATE_FILE = os.path.join(os.path.dirname(__file__), "activated.json")
    
ACTIVATE_PATH = os.path.join(base_dir, "data")
os.makedirs(ACTIVATE_PATH, exist_ok=True)  # 如果不存在就创建
APP_CONFIG_FILE = os.path.join(ACTIVATE_PATH, "app_config.json")


def load_app_config():
    default_config = {
        "dy_room_id": "",
        "dy_cookie": "",
    }
    if not os.path.exists(APP_CONFIG_FILE):
        return default_config
    try:
        with open(APP_CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            default_config["dy_room_id"] = str(data.get("dy_room_id", "") or "")
            default_config["dy_cookie"] = str(data.get("dy_cookie", "") or "")
    except Exception as e:
        logger.error(f"读取应用配置失败: {e}")
    return default_config


def save_app_config(config: dict):
    safe_config = {
        "dy_room_id": str(config.get("dy_room_id", "") or "").strip(),
        "dy_cookie": str(config.get("dy_cookie", "") or "").strip(),
    }
    with open(APP_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(safe_config, f, ensure_ascii=False, indent=2)


# ---------- 对外推送服务（GUI 进程内，端口 8765） ----------
# 数据流：WebSocketServerD（本进程内唯一抓取器）-> dyDataReceiver 分两路：
#   1) MyWindow.onDyDataReceiver         -> 界面显示 / 语音 / 小票打印
#   2) LiveFetcherService -> LiveWebSocketServer.broadcast(8765) -> 本机浏览器 / python 脚本
# 不再拉起独立 live_server 子进程，也不需要 GUI 侧的 WS 客户端。
# 对外 WS 端口全项目唯一，统一定义于 server/live_server.py，此处只引用、不再硬编码
LIVE_SERVER_PORT = WS_PORT

log_dir = os.path.join(base_dir, "log")
os.makedirs(log_dir, exist_ok=True)


def _port_busy(port: int = LIVE_SERVER_PORT) -> bool:
    """8765 是否被别的进程占着（例如旧版独立 live_server 残留）。"""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("0.0.0.0", port))
        return False
    except OSError:
        return True
    finally:
        s.close()


# 激活码存取 相关（激活功能已移除，以下函数统一屏蔽，不再被任何逻辑调用）
# def save_act_code(code: str): ...
# def load_act_code(): ...
# def clear_act_code(): ...
# 激活码存取 相关 end
 
# 日志相关 start 
# 创建 logger
logger = logging.getLogger()
logger.setLevel(logging.INFO)
# log 文件夹已在模块顶部创建，这里直接复用
os.makedirs(log_dir, exist_ok=True)  # 如果不存在就创建

# 最终日志文件路径
log_file = os.path.join(log_dir, "actionlog.txt")
# 使用 TimedRotatingFileHandler，每 10 分钟切割一次
handler = TimedRotatingFileHandler(
    filename=log_file,
    when="M",      # 按分钟切割
    interval=10,   # 每 10 分钟
    backupCount=0, # 保留旧日志文件数量，0 表示无限
    encoding="utf-8",
    utc=False
)

# 文件名时间戳格式，例如：actionlog.txt -> actionlog.txt.2025-08-21_10-00
handler.suffix = "%Y-%m-%d_%H-%M"

# 设置日志格式
formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
handler.setFormatter(formatter)

logger.addHandler(handler)

logger.info(f"日志初始化:{log_file}")
# 日志相关 end 
def global_except_hook(exc_type, exc_value, exc_tb):
    """任何线程崩溃都会走到这里"""
    err = ''.join(traceback.format_exception(exc_type, exc_value, exc_tb))
    # 立即写到同一个 logger
    logger.critical("Uncaught exception (process will exit):\n%s", err)
    # 强制刷盘，防止 PyInstaller 闪退时丢失
    for h in logger.handlers:
        h.flush()
        if hasattr(h, 'stream') and hasattr(h.stream, 'flush'):
            h.stream.flush()

# 主线程
sys.excepthook = global_except_hook

# 子线程（Py3.8+ 有效）
def patch_thread_run():
    if hasattr(threading.Thread, '_bootstrap'):
        orig_bootstrap = threading.Thread._bootstrap
        def safe_bootstrap(self):
            try:
                orig_bootstrap(self)
            except Exception:
                global_except_hook(*sys.exc_info())
                raise   # 继续向上抛，线程真正结束
        threading.Thread._bootstrap = safe_bootstrap
patch_thread_run()
# ----------------------------------



# 获取设备唯一编码 start
def get_motherboard_serial():
    """获取设备唯一编码（支持 Windows / macOS / Linux）"""
    system = platform.system()
    try:
        if system == "Windows":
            def run_ps(command: str) -> str:
                try:
                    result = subprocess.check_output(
                        ["powershell", "-NoProfile", "-Command", command],
                        stderr=subprocess.STDOUT
                    )
                    return 'live-'+result.decode("utf-8", errors="ignore").strip()
                except Exception:
                    return ""

            # 1. 主板序列号
            serial = run_ps("(Get-CimInstance Win32_BaseBoard).SerialNumber")
            if serial:
                return serial

            # 2. BIOS 序列号
            serial = run_ps("(Get-CimInstance Win32_BIOS).SerialNumber")
            if serial:
                return serial

            # 3. CPU ID
            serial = run_ps("(Get-CimInstance Win32_Processor).ProcessorId")
            if serial:
                return serial

            # 4. 兜底：持久 GUID
            path = os.path.join(os.getenv("APPDATA") or ".", "device_guid.txt")
            if os.path.exists(path):
                with open(path, "r") as f:
                    guid = f.read().strip()
            else:
                guid = str(uuid.uuid4())
                with open(path, "w") as f:
                    f.write(guid)
            return f"GUID:{guid}"

        elif system == "Darwin":  # macOS
            serial = None

            # 1. system_profiler（优先）
            try:
                result = subprocess.check_output(
                    ["system_profiler", "SPHardwareDataType"],
                    encoding="utf-8", errors="ignore"
                )
                for line in result.splitlines():
                    if "Serial Number" in line:
                        serial = line.split(":")[-1].strip()
                        break
            except Exception:
                pass

            # 2. ioreg（备用）
            if not serial:
                try:
                    result = subprocess.check_output(
                        ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                        encoding="utf-8", errors="ignore"
                    )
                    for line in result.splitlines():
                        if "IOPlatformSerialNumber" in line:
                            serial = line.split("=")[-1].strip().strip('"')
                            break
                except Exception:
                    pass

            # 3. 兜底（uuid）
            if not serial:
                serial = str(uuid.getnode())

            return 'live-'+serial

        elif system == "Linux":
            try:
                with open("/sys/devices/virtual/dmi/id/board_serial", "r") as f:
                    serial = f.read().strip()
                return 'live-'+serial if serial else str(uuid.getnode())
            except Exception:
                return str(uuid.getnode())

        else:
            return "Unsupported OS"

    except Exception as e:
        return f"Error: {str(e)}"
# 获取设备唯一编码 end


# 对外 WS 端口全项目唯一（8765）；原第二个 WS 端口及其 AsyncThread 已整体移除。
# 对外推送 WS(8765) 现由本进程在 on_start 里直接启动，数据来自本进程的 WebSocketServerD。


# 主窗口类
class MyWindow(QWidget):
    # 新增一个信号用于在主线程中转发消息
    forward_message = pyqtSignal(str)
    forward_message_detail = pyqtSignal(str, str)
    # 后台线程 → 主线程 的 UI 调度通道（承载一个无参回调）
    run_on_ui = pyqtSignal(object)
    
    def __init__(self):
        super().__init__()
        # 单一数据源：WebSocketServerD 在本进程内抓取，dyDataReceiver 一路给界面、
        # 一路经 LiveWebSocketServer.broadcast 从 8765 推给外部浏览器 / python 脚本。
        self.live_service = None    # LiveFetcherService（持有唯一 DouyinLiveWebFetcher）
        self.live_ws = None         # LiveWebSocketServer(8765)
        self.print_mgr = print_mgr   # 事件小票打印机（USB，兼容 mac/win，多类型）
        self.deviceId = get_motherboard_serial()
        # 关键修复：连接转发信号
        self.forward_message.connect(self.append_message)
        self.forward_message_detail.connect(self.append_message)
        # 后台线程 UI 调度（激活请求已移出主线程，弹窗/按钮状态需回主线程）
        self.run_on_ui.connect(self._exec_on_ui)
        self.code = None
        self.app_config = load_app_config()
        self.initUI()
        
        self.updater = Updater(self)
        
        self.http_server = DyHttpServer()
        
        pkgs = ["PyQt5", "websockets", "pyserial", "requests","edge_tts","playsound","pygame","py_mini_racer"]   # 按需增删
        
        for pkg in pkgs:
            try:
                ver = meta.version(pkg)
                logging.info("Dependency %-15s %s", pkg, ver)
            except meta.PackageNotFoundError:
                logging.warning("Dependency %-15s not found", pkg)
                
    def check_update_version(self):
       
        has_update, latest, url, log = self.updater.check_update()
        if not has_update:
            QMessageBox.warning(self,"提示", "已是最新版")   
            return

        ok = QMessageBox.question(
            self, "发现新版本",
            f"检测到新版本 {latest}\n更新内容:\n{log}\n是否立即更新？",
            QMessageBox.Yes | QMessageBox.No
        )
        if ok != QMessageBox.Yes:
            return

        # 1. 把参数写 JSON
        cfg = {
            "download_url": url,
            "target_dir": os.path.dirname(sys.executable),
            "exe_name": "liveMain.exe"
        }
        # 写 JSON
        cfg_path = os.path.join(tempfile.gettempdir(), "liveMain_update.json")
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f)

        # 直接启动同目录下的 updater.exe
        from PyQt5.QtCore import QProcess
        updater_exe = os.path.join(os.path.dirname(sys.executable), "updater.exe")
        QProcess.startDetached(updater_exe)
        QApplication.quit()
    def initUI(self):
        # self.setWindowTitle("直播互动控制器")
        self.setWindowTitle(AppState.live_title)
        self.resize(800, 600)

         # 添加串口选择区域
        self.serial_label = QLabel("串口选择:")
        self.serial_combo = QComboBox()
        self.refresh_serial_btn = QPushButton("刷新")
        self.connect_serial_btn = QPushButton("连接")
        self.btn_lock_cfg = QPushButton("设置")
        

         # 串口布局
        serial_layout = QHBoxLayout()
        serial_layout.addWidget(self.serial_label)
        serial_layout.addWidget(self.serial_combo)
        serial_layout.addWidget(self.refresh_serial_btn)
        serial_layout.addWidget(self.connect_serial_btn)
        serial_layout.addWidget(self.btn_lock_cfg)
        
        # WebSocket地址显示
        self.address_label = QLabel(f"服务地址: ws://127.0.0.1:{LIVE_SERVER_PORT}（网页与本程序共用）")
        self.address_label.setStyleSheet("font-size:14px; color:#2c3e50; font-weight:bold;")

        # 服务地址行（激活功能已移除：不再显示设备码 / 激活码输入框 / 激活按钮）
        addr_layout = QHBoxLayout()
        addr_layout.addWidget(self.address_label)
        addr_layout.addStretch()
        
        self.dy_live_roomLable = QLabel(f"抖音房间号:")
        self.dy_live_roomid = QLineEdit()
        self.dy_live_roomid.setPlaceholderText("请输入抖音房间号")
        self.dy_live_roomid.setMaximumWidth(150)
        room_id_layout = QHBoxLayout()
        room_id_layout.addWidget(self.dy_live_roomLable)
        room_id_layout.addWidget(self.dy_live_roomid)
        room_id_layout.addStretch()

        self.dy_cookie_label = QLabel("抖音Cookie:")
        self.dy_cookie_input = QLineEdit()
        self.dy_cookie_input.setPlaceholderText("必须粘贴 www.douyin.com 登录后的完整 Cookie（含 sessionid / sid_tt）；游客 Cookie 收不到礼物")
        self.dy_cookie_auto_btn = QPushButton("从浏览器提取")
        self.dy_cookie_auto_btn.setToolTip(
            "自动读取本机浏览器（Edge / Chrome / Firefox）里已登录的抖音 Cookie 并填入左侧输入框")
        self.dy_cookie_auto_btn.clicked.connect(self.on_auto_cookie_clicked)
        cookie_layout = QHBoxLayout()
        cookie_layout.addWidget(self.dy_cookie_label)
        cookie_layout.addWidget(self.dy_cookie_input)
        cookie_layout.addWidget(self.dy_cookie_auto_btn)
        
        # 消息显示区域
        self.msg_label = QLabel("消息记录:")
        self.show_gift  = QCheckBox("礼物")
        self.show_gift.setChecked(True)      # 消息记录：默认仅勾选「礼物」
        self.show_msg  = QCheckBox("聊天")
        self.show_like = QCheckBox("点赞")
        self.show_follow = QCheckBox("关注")
        self.show_enter = QCheckBox("进场")
        self.show_stats = QCheckBox("统计")
        self.show_fans = QCheckBox("粉丝团")
        self.msg_label_voice = QLabel("语音播报:")
        self.show_gift_voice  = QCheckBox("礼物")     # 语音播报：默认全部不勾
        self.show_msg_voice  = QCheckBox("聊天")
        self.show_good_voice  = QCheckBox("点赞")
        self.show_follow_voice = QCheckBox("关注")
        self.show_in_voice    = QCheckBox("进场")
        filter_msg_gif = QHBoxLayout()
        filter_msg_gif.addWidget(self.msg_label)
        filter_msg_gif.addWidget(self.show_gift)
        filter_msg_gif.addWidget(self.show_msg)
        filter_msg_gif.addStretch()                # 把按钮推到最右

        # 语音播报（与“显示”分组独立成行）
        filter_msg_voice = QHBoxLayout()
        filter_msg_voice.addWidget(self.msg_label_voice)
        filter_msg_voice.addWidget(self.show_gift_voice)
        filter_msg_voice.addWidget(self.show_msg_voice)
        filter_msg_voice.addWidget(self.show_good_voice)
        filter_msg_voice.addWidget(self.show_follow_voice)
        filter_msg_voice.addWidget(self.show_in_voice)
        filter_msg_voice.addStretch()
        # 清空语音队列按钮（位于本行最右侧）
        self.btn_clear_voice = QPushButton("清空队列")
        self.btn_clear_voice.clicked.connect(self.on_clear_voice_queue)
        filter_msg_voice.addWidget(self.btn_clear_voice)
        # 大礼物「优先语音 + 优先打印」设置按钮（位于「清空队列」右侧）
        self.btn_priority_settings = QPushButton("设置")
        self.btn_priority_settings.clicked.connect(self.open_priority_settings)
        filter_msg_voice.addWidget(self.btn_priority_settings)

        filter_msg_more = QHBoxLayout()
        filter_msg_more.addWidget(QLabel("更多来源:"))
        filter_msg_more.addWidget(self.show_like)
        filter_msg_more.addWidget(self.show_follow)
        filter_msg_more.addWidget(self.show_enter)
        filter_msg_more.addWidget(self.show_stats)
        filter_msg_more.addWidget(self.show_fans)
        filter_msg_more.addStretch()

        # 打印开关（USB 小票，按类型独立勾选，与“显示”开关互不影响）
        self.print_label = QLabel("打印:")
        self.print_chat = QCheckBox("聊天")
        self.print_gift = QCheckBox("礼物")
        # self.print_gift.setChecked(True)      # 保持此前“礼物即打印”的默认行为
        self.print_like = QCheckBox("点赞")
        self.print_enter = QCheckBox("进场")
        self.print_follow = QCheckBox("关注")
        self.print_fans = QCheckBox("粉丝团")
        filter_print = QHBoxLayout()
        filter_print.addWidget(self.print_label)
        filter_print.addWidget(self.print_chat)
        filter_print.addWidget(self.print_gift)
        filter_print.addWidget(self.print_like)
        filter_print.addWidget(self.print_enter)
        filter_print.addWidget(self.print_follow)
        filter_print.addWidget(self.print_fans)
        filter_print.addSpacing(12)
        self.btn_open_sim = QPushButton("小票模拟")
        self.btn_open_sim.setCursor(Qt.PointingHandCursor)
        self.btn_open_sim.setToolTip("打开小票模拟（PC 模式）独立窗口")
        self.btn_open_sim.clicked.connect(self.open_receipt_window)
        filter_print.addWidget(self.btn_open_sim)
        self.btn_open_phone = QPushButton("手机预览")
        self.btn_open_phone.setCursor(Qt.PointingHandCursor)
        self.btn_open_phone.setToolTip("打开手机预览（手机模式）独立窗口")
        self.btn_open_phone.clicked.connect(lambda: self.receipt_page.open_phone_window())
        filter_print.addWidget(self.btn_open_phone)
        self.btn_open_3d = QPushButton("3D打印")
        self.btn_open_3d.setCursor(Qt.PointingHandCursor)
        self.btn_open_3d.setToolTip("打开 3D打印现场：龙门架逐层打印观众头像牌，成品转运到前方传送带")
        self.btn_open_3d.clicked.connect(lambda: self.receipt_page.open_printer3d_window())
        filter_print.addWidget(self.btn_open_3d)
        filter_print.addStretch()

        self.msg_display = QTextEdit()
        self.msg_display.setReadOnly(True)
        self.msg_display.setAcceptRichText(True)  # 显式启用富文本
        self.msg_display.setTextInteractionFlags(
            Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard
        )
        self.msg_display.setStyleSheet("background:#f8f9fa; font-family:Consolas; font-size:12px; padding:10px;")
        self.msg_display.setMinimumHeight(240)

        
        
        # 功能按钮
        self.start_btn = QPushButton("启动服务")
        self.stop_btn = QPushButton("停止服务")
        self.start_btn.setStyleSheet("padding:6px 14px; font-size:13px;")
        self.stop_btn.setStyleSheet("padding:6px 14px; font-size:13px;")
        self.stop_btn.setEnabled(False)

        # ---------- 过滤复选框 ----------
        # self.chk_gift  = QCheckBox("礼物")
        # self.chk_gift.setChecked(True)
        # self.ws_manager.filter_flags["礼物"] = True
        # self.chk_like  = QCheckBox("点赞")
        # self.chk_light = QCheckBox("点亮")
        # self.chk_danmu = QCheckBox("弹幕")
        
        # for chk in (self.chk_gift, self.chk_like, self.chk_light, self.chk_danmu):
        #     chk.toggled.connect(self.on_filter_changed)
        
        # filter_layout = QHBoxLayout()
        # filter_layout.addWidget(QLabel("过滤条件:"))
        # filter_layout.addWidget(self.chk_gift)
        # filter_layout.addWidget(self.chk_like)
        # filter_layout.addWidget(self.chk_light)
        # filter_layout.addWidget(self.chk_danmu)
        # filter_layout.addStretch()
        
        
        # 直播平台
        self.chk_ks  = QCheckBox("快手")          # 直播平台：默认仅勾选「抖音」
        self.chk_dy  = QCheckBox("抖音")
        self.chk_dy.setChecked(True)
        
        
        self.version_label = QLabel(f"当前版本:{AppState.get_live_version()}")
        self.check_update = QPushButton("检查更新")
        
        live_pts_layout = QHBoxLayout()
        live_pts_layout.addWidget(QLabel("直播平台:"))
        live_pts_layout.addWidget(self.chk_ks)
        live_pts_layout.addWidget(self.chk_dy)
        live_pts_layout.addStretch()
        live_pts_layout.addWidget(self.version_label)
        live_pts_layout.addWidget(self.check_update)
        
        # 布局
        btn_layout = QHBoxLayout()
        btn_layout.addWidget(self.start_btn)
        btn_layout.addWidget(self.stop_btn)
        btn_layout.setSpacing(12)

        
        
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(8)


        main_layout.addLayout(serial_layout)
        main_layout.addLayout(addr_layout)
        # main_layout.addWidget(self.address_label)
        main_layout.addLayout(room_id_layout)
        main_layout.addLayout(cookie_layout)
        main_layout.addLayout(btn_layout)
        # main_layout.addWidget(self.msg_label)
        main_layout.addLayout(filter_msg_gif)
        main_layout.addLayout(filter_msg_more)
        main_layout.addLayout(filter_msg_voice)
        main_layout.addLayout(filter_print)

        main_layout.addWidget(self.msg_display)
        main_layout.addLayout(live_pts_layout)
        # main_layout.addLayout(filter_layout)

        # 不再用顶部 tab：控制台页面直接作为本 widget 布局；
        # 小票模拟(PC) 与 手机预览(手机) 改为「打印」行右侧按钮，各自弹独立窗口
        # （self.receipt_page 实例保留，供 append_message 喂数据 + 手机预览回放）
        self.receipt_page = ReceiptSimulator()

        # 外层滚动容器：内容超出窗口高度时自动滚动，保证小屏也能看全全部控件
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea{border:none; background:transparent;}")
        content = QWidget()
        content.setLayout(main_layout)
        scroll.setWidget(content)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        # 初始/默认窗口尺寸收敛；内容更高时窗口内滚动而非撑出屏幕
        self.resize(820, 640)

        self.dy_live_roomid.setText(self.app_config.get("dy_room_id", ""))
        self.dy_cookie_input.setText(self.app_config.get("dy_cookie", ""))

        # 绑定按钮事件
        self.refresh_serial_btn.clicked.connect(self.refresh_serial_ports)
        self.connect_serial_btn.clicked.connect(self.connect_serial_port)
        self.btn_lock_cfg.clicked.connect(self.open_lock_cfg)
        self.start_btn.clicked.connect(self.on_start)
        self.stop_btn.clicked.connect(self.on_stop)
        
        self.show_gift.toggled.connect(self.on_checkbox_changed)
        self.show_msg.toggled.connect(self.on_checkbox_changed)
        self.show_like.toggled.connect(self.on_checkbox_changed)
        self.show_follow.toggled.connect(self.on_checkbox_changed)
        self.show_enter.toggled.connect(self.on_checkbox_changed)
        self.show_stats.toggled.connect(self.on_checkbox_changed)
        self.show_fans.toggled.connect(self.on_checkbox_changed)
        self.show_gift_voice.toggled.connect(self.on_checkbox_changed)
        self.show_msg_voice.toggled.connect(self.on_checkbox_changed)
        self.show_good_voice.toggled.connect(self.on_checkbox_changed)
        self.show_follow_voice.toggled.connect(self.on_checkbox_changed)
        self.show_in_voice.toggled.connect(self.on_checkbox_changed)
        self.chk_ks.toggled.connect(self.on_checkbox_changed)
        self.chk_dy.toggled.connect(self.on_checkbox_changed)
        # 打印类型勾选框同步到 AppState
        self.print_chat.toggled.connect(self.on_checkbox_changed)
        self.print_gift.toggled.connect(self.on_checkbox_changed)
        self.print_like.toggled.connect(self.on_checkbox_changed)
        self.print_enter.toggled.connect(self.on_checkbox_changed)
        self.print_follow.toggled.connect(self.on_checkbox_changed)
        self.print_fans.toggled.connect(self.on_checkbox_changed)
        self.check_update.clicked.connect(self.check_update_version)
        self.dy_live_roomid.editingFinished.connect(self.save_runtime_config)
        self.dy_cookie_input.editingFinished.connect(self.save_runtime_config)

        AppState.set_show_gift(self.show_gift.isChecked())
        AppState.set_show_msg(self.show_msg.isChecked())
        AppState.set_show_like(self.show_like.isChecked())
        AppState.set_show_follow(self.show_follow.isChecked())
        AppState.set_show_enter(self.show_enter.isChecked())
        AppState.set_show_stats(self.show_stats.isChecked())
        AppState.set_show_fans(self.show_fans.isChecked())

        # 语音播报勾选初始状态同步到 AppState
        AppState.set_show_gift_voice(self.show_gift_voice.isChecked())
        AppState.set_show_msg_voice(self.show_msg_voice.isChecked())
        AppState.set_show_like_voice(self.show_good_voice.isChecked())
        AppState.set_show_follow_voice(self.show_follow_voice.isChecked())
        AppState.set_show_enter_voice(self.show_in_voice.isChecked())

        # 打印类型勾选初始状态同步到 AppState
        AppState.set_show_chat_print(self.print_chat.isChecked())
        AppState.set_show_gift_print(self.print_gift.isChecked())
        AppState.set_show_like_print(self.print_like.isChecked())
        AppState.set_show_enter_print(self.print_enter.isChecked())
        AppState.set_show_follow_print(self.print_follow.isChecked())
        AppState.set_show_fans_print(self.print_fans.isChecked())

        # 初始化时刷新串口列表
        self.refresh_serial_ports()

        # 8765 由本进程在「启动服务」时占用；旧版独立 live_server 若残留占着端口，
        # on_start 会在界面提示，不再自动杀进程。

    # 通用勾选回调
    def on_checkbox_changed(self, checked):
        sender = self.sender()  # 获取触发的 QCheckBox
        if sender == self.show_gift:
            AppState.set_show_gift(checked)
            print(f"on_checkbox_changed 礼物勾选状态: {checked}")
        elif sender == self.show_msg:
            AppState.set_show_msg(checked)
            print(f"on_checkbox_changed 聊天勾选状态: {checked}")    
        elif sender == self.show_like:
            AppState.set_show_like(checked)
            print(f"on_checkbox_changed 点赞勾选状态: {checked}")
        elif sender == self.show_follow:
            AppState.set_show_follow(checked)
            print(f"on_checkbox_changed 关注勾选状态: {checked}")
        elif sender == self.show_enter:
            AppState.set_show_enter(checked)
            print(f"on_checkbox_changed 进场勾选状态: {checked}")
        elif sender == self.show_stats:
            AppState.set_show_stats(checked)
            print(f"on_checkbox_changed 统计勾选状态: {checked}")
        elif sender == self.show_fans:
            AppState.set_show_fans(checked)
            print(f"on_checkbox_changed 粉丝团勾选状态: {checked}")
        elif sender == self.show_gift_voice:
            AppState.set_show_gift_voice(checked)
            print(f"on_checkbox_changed 礼物语音勾选状态: {checked}")
        elif sender == self.show_msg_voice:
            AppState.set_show_msg_voice(checked)
            print(f"on_checkbox_changed 聊天语音勾选状态: {checked}")
        elif sender == self.show_good_voice:
            AppState.set_show_like_voice(checked)
            print(f"on_checkbox_changed 点赞语音勾选状态: {checked}")
        elif sender == self.show_follow_voice:
            AppState.set_show_follow_voice(checked)
            print(f"on_checkbox_changed 关注语音勾选状态: {checked}")
        elif sender == self.show_in_voice:
            AppState.set_show_enter_voice(checked)
            print(f"on_checkbox_changed 进场语音勾选状态: {checked}")
        elif sender == self.chk_ks:
            AppState.set_chk_ks(checked)
            print(f"on_checkbox_changed 快手勾选状态: {checked}")
        elif sender == self.chk_dy:
            AppState.set_chk_dy(checked)
            print(f"on_checkbox_changed 抖音勾选状态: {checked}")
        elif sender == self.print_chat:
            AppState.set_show_chat_print(checked)
            print(f"on_checkbox_changed 聊天打印勾选状态: {checked}")
        elif sender == self.print_gift:
            AppState.set_show_gift_print(checked)
            print(f"on_checkbox_changed 礼物打印勾选状态: {checked}")
        elif sender == self.print_like:
            AppState.set_show_like_print(checked)
            print(f"on_checkbox_changed 点赞打印勾选状态: {checked}")
        elif sender == self.print_enter:
            AppState.set_show_enter_print(checked)
            print(f"on_checkbox_changed 进场打印勾选状态: {checked}")
        elif sender == self.print_follow:
            AppState.set_show_follow_print(checked)
            print(f"on_checkbox_changed 关注打印勾选状态: {checked}")
        elif sender == self.print_fans:
            AppState.set_show_fans_print(checked)
            print(f"on_checkbox_changed 粉丝团打印勾选状态: {checked}")

    def on_clear_voice_queue(self):
        """点击「清空队列」：停止当前播放并清空 TTS 待播队列"""
        try:
            TTSManager.stop_all()
            self.append_message("语音队列已清空")
        except Exception as e:
            logging.exception("清空语音队列失败: %s", e)

    def open_priority_settings(self):
        """大礼物「优先语音 + 优先打印」设置对话框。

        可输入礼物价值阈值（钻），当礼物钻价值 ≥ 该值时，
        插入队列最前方，优先语音播报 + 优先打印（不打断当前正在播放的语音）。
        """
        dlg = QDialog(self)
        dlg.setWindowTitle("大礼物优先设置")
        dlg.setMinimumWidth(340)
        layout = QVBoxLayout(dlg)

        # 阈值输入
        th_box = QHBoxLayout()
        th_label = QLabel("礼物价值阈值(钻):")
        th_edit = QLineEdit(str(AppState.get_high_value_threshold()))
        th_box.addWidget(th_label)
        th_box.addWidget(th_edit, 1)
        layout.addLayout(th_box)

        hint = QLabel(
            "礼物钻价值 ≥ 该值时，插到队列最前方优先播报 + 优先打印"
            "（不打断当前正在播放的语音）。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray; font-size: 11px;")
        layout.addWidget(hint)

        layout.addSpacing(8)

        # 两个开关
        chk_voice = QCheckBox("优先语音播报")
        chk_voice.setChecked(AppState.get_high_value_priority_voice())
        chk_print = QCheckBox("优先打印")
        chk_print.setChecked(AppState.get_high_value_priority_print())
        layout.addWidget(chk_voice)
        layout.addWidget(chk_print)

        layout.addSpacing(8)

        # 确定 / 取消
        btn_box = QHBoxLayout()
        btn_ok = QPushButton("确定")
        btn_cancel = QPushButton("取消")
        btn_box.addStretch()
        btn_box.addWidget(btn_ok)
        btn_box.addWidget(btn_cancel)
        layout.addLayout(btn_box)

        def on_ok():
            try:
                val = int(th_edit.text())
            except (TypeError, ValueError):
                val = AppState.get_high_value_threshold()
            AppState.set_high_value_threshold(val)
            AppState.set_high_value_priority_voice(chk_voice.isChecked())
            AppState.set_high_value_priority_print(chk_print.isChecked())
            self.append_message(
                "大礼物优先设置已更新：阈值=%d钻，优先语音=%s，优先打印=%s"
                % (val,
                   "开" if chk_voice.isChecked() else "关",
                   "开" if chk_print.isChecked() else "关")
            )
            dlg.accept()

        btn_ok.clicked.connect(on_ok)
        btn_cancel.clicked.connect(dlg.reject)

        dlg.exec_()
    def open_receipt_window(self):
        """打开「小票模拟（PC 模式）」独立窗口（懒加载，复用不重建）。"""
        if getattr(self, "receipt_window", None) is None:
            w = QDialog(self)
            w.setWindowTitle("小票模拟（PC 模式）")
            w.resize(580, 780)
            lay = QVBoxLayout(w)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.addWidget(self.receipt_page)
            self.receipt_window = w
        self.receipt_window.show()
        self.receipt_window.raise_()
        self.receipt_window.activateWindow()
    def open_lock_cfg(self):
        if self.connect_serial_btn.text() == "断开":
            self.lock_cfg_win = LockSettingsWidget(self)
            self.lock_cfg_win.settings_saved.connect(self.apply_lock_cfg)  # 可选
            self.lock_cfg_win.show()   
        else:
            self.append_message("请先连接串口")
            QMessageBox.critical(self, "提示", "请先连接串口")   
    def apply_lock_cfg(self, cfg):
        # 例如把配置同步给 SerialManager
        print("新的锁控配置:", cfg) 
    # def check_device_status(self):
    #     """启动检查：本地只存有激活码，是否过期完全由服务器判定。
    #     读取本地激活码后交给后台线程向服务器校验。"""
    #     ...（激活功能已移除，本方法已屏蔽，启动时不再做激活校验）
    def check_device_status(self):
        # 激活功能已移除：不再向服务器校验激活状态，方法保留为空操作以防外部误调用
        pass
    def refresh_serial_ports(self):
        """刷新串口列表"""
        self.serial_combo.clear()
        ports = get_available_ports()
        if ports:
            self.serial_combo.addItems(ports)
            self.append_message(f"发现 {len(ports)} 个可用串口,请手动连接正确COM口")
        else:
            self.append_message("未发现可用串口")

    def connect_serial_port(self):
        """连接选中的串口"""
        selected = self.serial_combo.currentText()
        if not selected:
            QMessageBox.warning(self, "提示", "请选择串口")
            return
            
        # 提取端口号（如从"COM6 - USB Serial Port"中提取"COM6"）
        port = selected.split(" - ")[0]
        
        # from SerialManager import serial_manager
        if serial_manager.connect(port):
            self.append_message(f"串口 {port} 连接成功")
            self.connect_serial_btn.setText("断开")
            self.connect_serial_btn.clicked.disconnect()
            self.connect_serial_btn.clicked.connect(self.disconnect_serial_port)
        else:
            self.append_message(f"串口 {port} 连接失败")

    def disconnect_serial_port(self):
        """断开当前串口连接"""
        # from SerialManager import serial_manager
        serial_manager.close()
        self.append_message("串口已断开")
        self.connect_serial_btn.setText("连接")
        self.connect_serial_btn.clicked.disconnect()
        self.connect_serial_btn.clicked.connect(self.connect_serial_port)
    #添加设备（仅向服务器注册本机，幂等；不做 UI、不触发激活）
#     def add_device(self):
#         url = "http://www.zhengduimen.com/app/admin/device/add_device"

#         # 构造参数
#         data = {
#             "device_id": self.deviceId,
#             "device_version": AppState.get_live_version(),
#             "device_time": time.strftime("%Y-%m-%d", time.localtime())
#         }

#         try:
#             response = requests.post(url, data=data, timeout=10)
#             if response.status_code != 200:
#                 logger.warning("add_device 状态码异常: %s", response.status_code)
#                 return None
#             return response.json()
#         except Exception as e:
#             logger.exception("add_device 请求失败: %s", e)
#             return None
    def _ui(self, fn):
        """在主线程执行 UI 操作；已在主线程则直接执行，否则经信号队列调度回去。
        供激活等后台线程安全更新弹窗/按钮状态。"""
        if self.thread() == QThread.currentThread():
            fn()
        else:
            self.run_on_ui.emit(fn)

    @pyqtSlot(object)
    def _exec_on_ui(self, fn):
        try:
            fn()
        except Exception:
            logger.exception("run_on_ui 回调执行失败")

#     def setActBtStatus(self,clickStatus):
#         if clickStatus==0:   
#             self.activate_btn.setText("激活")
#             self.activate_btn.setEnabled(True)
#         elif clickStatus==1:
#             self.activate_btn.setText("激活中...")
#             self.activate_btn.setEnabled(False)
#         elif clickStatus==2:
#             self.activate_btn.setText("已激活")
#             self.activate_btn.setEnabled(False)
    # 激活/校验设备（统一入口：先注册设备，再向服务器请求激活）
    # 过期与否完全由服务器判定，本地只保存激活码。
    # verify_only=True 表示“启动时用本地已有激活码向服务器校验”，此时网络异常不清除本地信息。
#     def verify_and_activate(self, code: str, verify_only: bool = False):
#         self.code = code
#         # 1) 先向服务器注册设备（幂等；已注册时服务器返回 code=2，不影响后续激活）
#         add_resp = self.add_device()
#         if add_resp is None:
#             logger.warning("add_device 失败（网络异常？），继续尝试激活（设备可能已注册）")

#         # 2) 没有激活码则无法激活，仅完成注册，等待用户输入
#         if not code:
#             self._ui(lambda: self.setActBtStatus(0))
#             return

#         # 3) 向服务器请求激活（admin001 等任何码都走这里，服务器判定有效期）
#         url = "http://www.zhengduimen.com/app/admin/device/act_device"
#         data = {
#             "device_id": self.deviceId,
#             "device_version": AppState.get_live_version(),
#             "device_time": time.strftime("%Y-%m-%d", time.localtime()),
#             "device_act": code
#         }
#         logger.info("liveMain 激活设备 %s", data)
#         try:
#             response = requests.post(url, data=data, timeout=10)
#             if response.status_code != 200:
#                 self._on_activate_network_error(verify_only, "网络异常")
#                 return
#             resp = response.json()
#             # 业务 code 非 0：激活失败（无效激活码等）——以服务器判定为准，清除本地
#             if resp.get("code") != 0:
#                 self._on_activate_failed(resp.get("msg", "激活失败"))
#                 return
#             info = resp.get("data")
#             if not info:
#                 self._on_activate_failed("设备未录入")
#                 return
#             # 服务器判定：device_status != 1 视为已过期/不可用
#             if info.get("device_status") != 1:
#                 self._on_activate_expired("激活已过期，请重新激活")
#                 return
#             # 激活成功：本地仅存激活码，状态/过期时间写入 AppState（服务器为准）
#             out_time = info.get("device_out_time")
#             AppState.set_activated(True)
#             AppState.set_act_code(code)
#             AppState.set_expire(out_time)
#             save_act_code(code)
#             self._ui(lambda: self.setActBtStatus(2))
#             self.append_message("设备激活成功")
#         except Exception as e:
#             logger.exception("act_device 请求失败: %s", e)
#             self._on_activate_network_error(verify_only, "请求失败！请联系管理员")

#     def _on_activate_failed(self, msg):
#         """激活未成功（无效激活码/服务器明确拒绝）：清除本地信息，回到未激活态。"""
#         AppState.set_activated(False)
#         AppState.set_act_code("")
#         AppState.set_expire(None)
#         clear_act_code()
#         self.append_message(msg)
#         self._ui(lambda m=msg: QMessageBox.warning(self, "提示", m))
#         self._ui(lambda: self.setActBtStatus(0))

#     def _on_activate_expired(self, msg):
#         """服务器判定已过期：提示过期状态并清除本地激活信息。"""
#         AppState.set_activated(False)
#         AppState.set_act_code("")
#         AppState.set_expire(None)
#         clear_act_code()
#         self.append_message(msg)
#         self._ui(lambda m=msg: QMessageBox.warning(self, "提示", m))
#         self._ui(lambda: self.setActBtStatus(0))

#     def _on_activate_network_error(self, verify_only, msg):
#         """网络/HTTP 异常：不应清除本地激活信息。
#         - 启动校验（verify_only）且本地曾有激活码：离线放行，保留激活状态，下次联网再校验；
#         - 其它情况：仅提示，不清除、不激活。"""
#         if verify_only and load_act_code():
#             AppState.set_activated(True)
#             AppState.set_act_code(load_act_code())
#             self._ui(lambda: self.setActBtStatus(2))
#             self.append_message(f"{msg}（离线放行，联网后重新校验）")
#         else:
#             self.append_message(msg)
#             self._ui(lambda m=msg: QMessageBox.warning(self, "提示", m))
#             self._ui(lambda: self.setActBtStatus(0))
    # 点击激活按钮
    @pyqtSlot()
#     def on_activate_clicked(self):
#         self.activate_btn.setEnabled(False)   # 防止连点
#         self.activate_btn.setText("激活中…")
#         self.code = self.action_code.text().strip()
#         if not self.code:
#             QMessageBox.warning(self, "提示", "设备激活码不能为空！")
#             self.setActBtStatus(0)   # 修复：原来此处未复位按钮，会一直停在“激活中…”
#             return
#         # 网络请求放后台线程，避免服务器不可达时 UI 卡死 10 秒
#         threading.Thread(target=self.verify_and_activate, args=(self.code,), daemon=True).start()
    @pyqtSlot(str)
    def on_message_received(self, message):
        """接收转发消息并确保在主线程中处理"""
        # 检查当前线程是否为主线程
        if QThread.currentThread() != self.thread():
            # 如果不是主线程，通过信号转发到主线程
            self.forward_message.emit(message)
        else:
            # 如果已经是主线程，直接显示
            self.append_message(message)
    
    # 收到礼物列表后展示
    @pyqtSlot(dict)
    def on_gift_list_received(self, data: dict):
        if not data:
            self.append_message("拉取失败或为空", source_hint="礼物列表")
            return
        # 这里只简单打印
        # logging.info(f"收到礼物列表: {data}")
        # 你可以按 data["data"] 解析成表格、下拉框等
        
                

    # ===== 本进程内：抓取器 + 对外 WS 服务端(8765) =====

    def _start_live_services(self, room_id: str):
        """组装唯一数据源：WebSocketServerD -> 界面 + 对外 WS(8765)。

        唯一抓取器 DouyinLiveWebFetcher 的 dyDataReceiver 挂两个槽：
          - LiveFetcherService._on_message（DirectConnection，抓取线程内）：
            dict 原样交给 LiveWebSocketServer.broadcast 推给外部浏览器 / python 脚本；
          - MyWindow.onDyDataReceiver（QueuedConnection，主线程）：
            界面显示 + 语音 + 小票打印，同样拿原始事件。
        不加工、不过滤、不补发；对外只有一个 WS 端口。
        """
        if _port_busy():
            self.append_message(
                f"端口 {LIVE_SERVER_PORT} 已被占用（多半是旧版独立 live_server 残留），"
                "请执行 scripts/kill_8765.ps1 或 bash scripts/kill_8765.sh 结束后再启动",
                source_hint="系统")
            logger.warning("端口 %s 被占用，对外 WS 无法启动", LIVE_SERVER_PORT)
            return False

        ws = LiveWebSocketServer(host="0.0.0.0", port=LIVE_SERVER_PORT)
        ws.start()
        if ws.serve_error is not None or not ws.wait_ready(timeout=5):
            self.append_message(
                f"对外 WS 启动失败：{ws.serve_error or '端口未就绪'}（{LIVE_SERVER_PORT}）",
                source_hint="系统")
            ws.stop()
            return False

        service = LiveFetcherService(
            room_id=room_id,
            cookie=self.dy_cookie_input.text().strip(),
            on_event=ws.broadcast,
        )
        # 第二路：原始 dict repr 直接给界面（契约与旧版 GUI 抓取器一致）
        service.fetcher.dyDataReceiver.connect(self.onDyDataReceiver)
        service.start()
        self.live_service, self.live_ws = service, ws
        self.append_message(
            f"对外推送已就绪：ws://127.0.0.1:{LIVE_SERVER_PORT}（浏览器 / python 脚本可连接）",
            source_hint="系统")
        return True

    def _stop_live_services(self):
        """停掉抓取器和对外 WS，释放 8765。可重复调用。"""
        if self.live_service is not None:
            try:
                self.live_service.stop()
            except Exception as e:
                logger.warning("停止抖音抓取器异常: %s", e)
        if self.live_ws is not None:
            try:
                self.live_ws.stop()
            except Exception as e:
                logger.warning("停止对外 WS 异常: %s", e)
        self.live_service = self.live_ws = None

    @pyqtSlot()
    def on_start(self):
        # 激活功能已移除：程序无需激活即可直接启动服务
        room_id = self.dy_live_roomid.text().strip()
        if not room_id:
            self.append_message("请先输入抖音直播房间号")
            QMessageBox.critical(self, "提示", "请先输入抖音直播房间号")
            return
        self.save_runtime_config()
        try:
            self.http_server.data_received.disconnect(self.on_data_received)
        except TypeError:
            pass

        self._stop_live_services()      # 防止重复启动叠加
        started = self._start_live_services(room_id)

        self.http_server.data_received.connect(self.on_data_received)
        self.http_server.start()
        if started:
            self.start_btn.setEnabled(False)
            self.stop_btn.setEnabled(True)
            logger.info("liveMain 服务已启动（数据源：本进程 WebSocketServerD；对外 ws://127.0.0.1:%s）",
                        LIVE_SERVER_PORT)
        else:
            self.start_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            logger.warning("liveMain 对外服务未启动，可修复后重新点击「启动服务」")

    @pyqtSlot()
    def on_stop(self):
        try:
            self._stop_live_services()
            self.http_server.stop()
            try:
                self.http_server.data_received.disconnect(self.on_data_received)
            except TypeError:
                pass
        except Exception as e:
            logger.exception("停止 liveMain 网络服务异常: %s", e)
        finally:
            self.start_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            logger.info("liveMain 网络服务已停止")

    @pyqtSlot(str)
    def onDyDataReceiver(self, data):
        """WebSocketServerD.action() 的原始事件：界面显示 + 小票打印。

        语音由抓取器 action() 自己按 AppState 播报，这里不重复处理。
        """
        self.append_message(str(data), source_hint="抖音WS")

    def save_runtime_config(self):
        self.app_config["dy_room_id"] = self.dy_live_roomid.text().strip()
        self.app_config["dy_cookie"] = self.dy_cookie_input.text().strip()
        try:
            save_app_config(self.app_config)
        except Exception as e:
            logger.error(f"保存应用配置失败: {e}")

    # ===== 一键从浏览器提取抖音登录态 Cookie（替代油猴子脚本手动复制） =====
    def on_auto_cookie_clicked(self):
        """读浏览器 Cookie 库可能涉及 DPAPI/VSS，放后台线程，避免卡住界面。"""
        self.dy_cookie_auto_btn.setEnabled(False)
        self.dy_cookie_auto_btn.setText("提取中...")
        threading.Thread(target=self._extract_cookie_bg, daemon=True).start()

    def _extract_cookie_bg(self):
        try:
            from utils.dy_cookie_import import extract_douyin_cookie
            info = extract_douyin_cookie()
        except Exception as e:
            msg = str(e)
            self._ui(lambda m=msg: self._auto_cookie_failed(m))
        else:
            self._ui(lambda i=info: self._auto_cookie_done(i))

    def _auto_cookie_done(self, info):
        self.dy_cookie_auto_btn.setEnabled(True)
        self.dy_cookie_auto_btn.setText("从浏览器提取")
        self.dy_cookie_input.setText(info["cookie"])
        self.save_runtime_config()
        text = ("已从 %s 自动提取登录态 Cookie：%d 个字段，命中 %s。\n"
                "重新启动服务后即可收到礼物消息。") % (
                    info["browser"], info["fields"], ",".join(info["login_keys"]))
        self.append_message(text.replace("\n", " "))
        # 只展示字段名，绝不弹窗展示 Cookie 值
        QMessageBox.information(self, "提取成功", text)

    def _auto_cookie_failed(self, msg):
        self.dy_cookie_auto_btn.setEnabled(True)
        self.dy_cookie_auto_btn.setText("从浏览器提取")
        self.append_message("自动提取 Cookie 失败：" + msg.splitlines()[0])
        logger.warning("自动提取 Cookie 失败: %s", msg)
        QMessageBox.warning(self, "提取失败", msg)

    # HTTP 服务连接 
    def on_data_received(self, data):
        self.append_message(str(data))
    # 旧的第二个 WS 端口已停用，AsyncThread / on_thread_ready /
    # on_websocket_started / on_websocket_stopped / on_error 一并移除。
    # 启停状态现在由 _start_live_services / _stop_live_services（本进程 8765）负责。

    def getGiftList(self,gifList):
        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "Accept-Encoding": "gzip, deflate, br",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
            "Cache-Control": "max-age=0",
            "Connection": "keep-alive",
            "Cookie": "clientid=3; did=web_6173d0f93508d03325bae8db40b91428",
            "Host": "live.kuaishou.com",
            "Sec-Ch-Ua": '"Not_A Brand";v="8", "Chromium";v="120", "Microsoft Edge";v="120"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0"
        }

        response = requests.get(gifList,headers=headers, timeout=8)
        # return response.json()
        # 1. 接口整体是否返回有效数据
        response = response.json()
        self.gift_list_received.emit(response)   # <── 这里把数据广播出去
    # 对象 礼物 聊天
    @pyqtSlot(dict)
    def on_chat_gift_received(self, data: dict):
        if not data:
            self.append_message("拉取失败或为空", source_hint="消息分发")
            return
        if isinstance(data, dict):
            logger.info(f"data['source'] == '礼物'{data['source'] == '礼物'}")
            if data['source'] == '礼物':
                if self.show_gift.isChecked():
                    logger.info(f"self.show_gift.isChecked(){self.show_gift.isChecked()}")
                    self.append_message(str(data), source_hint="消息分发")
            if data['source']=='聊天':
                logger.info("data['source']=='聊天'")
                if self.show_msg.isChecked():
                    logger.info(f"self.show_msg.isChecked(){self.show_msg.isChecked()}")
                    self.append_message(str(data), source_hint="消息分发")

    def _parse_structured_message(self, message):
        if isinstance(message, dict):
            return message
        if not isinstance(message, str):
            return None

        text = message.strip()
        if not text:
            return None

        # 真实抖音事件优先按 JSON 解析（昵称/弹幕常含引号、emoji、特殊字符，
        # 旧版用 repr + ast.literal_eval 会让这类事件整条解析失败、被静默丢弃）。
        # 仍兼容旧 repr 格式（兜底），保证历史数据源不中断。
        try:
            return json.loads(text)
        except Exception:
            pass
        try:
            obj = ast.literal_eval(text)
            if isinstance(obj, dict):
                return obj
        except Exception:
            return None
        return None

    def _should_display_message(self, message, source_hint=""):
        msg_obj = self._parse_structured_message(message)
        if not isinstance(msg_obj, dict):
            return True

        source = str(msg_obj.get("source", "") or "")
        if source == "礼物":
            return self.show_gift.isChecked()
        if source == "聊天":
            return self.show_msg.isChecked()
        if source == "点赞":
            return self.show_like.isChecked()
        if source == "关注":
            return self.show_follow.isChecked()
        if source == "进场":
            return self.show_enter.isChecked()
        if source == "统计":
            return self.show_stats.isChecked()
        if source == "粉丝":
            return self.show_fans.isChecked()
        return True

    def _escape_html(self, value):
        text = str(value if value is not None else "")
        return (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    def _build_message_line(self, message, source_hint=""):
        msg_obj = self._parse_structured_message(message)
        if isinstance(msg_obj, dict):
            source = str(msg_obj.get("source", "") or "")
            from_text = str(msg_obj.get("from", "") or "")
            nick = str(msg_obj.get("nickName", "") or "")

            if "抖音" in from_text:
                platform = "抖音"
            elif "快手" in from_text:
                platform = "快手"
            elif source_hint:
                platform = source_hint
            else:
                platform = "消息"

            if source == "礼物":
                # 注意：礼物价值(price)仅用于底层逻辑判断，不展示在消息监控面板，避免用户直观看到金额
                detail = f"{nick} 赠送:{msg_obj.get('giftName', '')};数量:{msg_obj.get('giftCount', 1)};头像:{msg_obj.get('avatar')}"
                label = f"{platform}/礼物"
            elif source == "聊天":
                detail = f"{nick}: {msg_obj.get('msg', '')}"
                label = f"{platform}/聊天"
            elif source == "点赞":
                detail = f"{nick} 点赞 x{msg_obj.get('goodCount', 0)}"
                label = f"{platform}/点赞"
            elif source == "关注":
                detail = f"{nick} 关注了主播"
                label = f"{platform}/关注"
            elif source == "进场":
                detail = f"{nick} 进入了直播间"
                label = f"{platform}/进场"
            elif source == "统计":
                detail = f"当前 {msg_obj.get('current', '-')}, 累计 {msg_obj.get('total', '-')}"
                label = f"{platform}/统计"
            elif source == "粉丝":
                detail = str(msg_obj.get("msg", ""))
                label = f"{platform}/粉丝团"
            else:
                detail = str(msg_obj)
                label = f"{platform}/{source or '对象'}"
            return label, detail

        text = str(message).strip()
        if source_hint == "抖音WS":
            if "直播间：" in text:
                return "抖音/状态", text
            return "抖音/系统", text
        if source_hint == "礼物列表":
            return "系统/礼物列表", text
        if source_hint == "消息分发":
            return "本地WS/分发", text

        if "串口" in text:
            return "系统/串口", text
        if "WebSocket服务" in text or "线程已就绪" in text or "客户端已连接" in text:
            return "系统/服务", text
        if "错误" in text:
            return "系统/错误", text
        return "系统/消息", text

    @pyqtSlot(str)
    @pyqtSlot(str, str)
    def append_message(self, message, source_hint=""):
        """确保在主线程中更新UI"""
        # 最终的UI更新确保在主线程
        if self.thread() != QThread.currentThread():
            self.forward_message_detail.emit(str(message), str(source_hint))
            return

        # 打印触发（独立开关，不受“显示”过滤影响；仅在主线程执行一次，避免跨线程转发重复打印）
        self._maybe_print(message)

        # 小票模拟打印页：与打印/显示勾选互不影响，收到结构化消息即出票
        receipt_page = getattr(self, "receipt_page", None)
        if receipt_page is not None:
            receipt_msg = self._parse_structured_message(message)
            if isinstance(receipt_msg, dict):
                receipt_page.add_event(receipt_msg)

        if not self._should_display_message(message, source_hint=source_hint):
            return

        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        label, detail = self._build_message_line(message, source_hint=source_hint)
        safe_label = self._escape_html(label)
        safe_detail = self._escape_html(detail)
        self.msg_display.append(
            f"[{timestamp}] "
            f"<span style='color:#1f6feb;font-weight:600;'>[{safe_label}]</span> "
            f"{safe_detail}"
        )
        self.msg_display.moveCursor(self.msg_display.textCursor().End)

    def _maybe_print(self, message):
        """按消息类型 + 打印勾选框，决定是否打印 USB 小票（58mm，后台线程不卡 UI）。"""
        msg_obj = self._parse_structured_message(message)
        if not isinstance(msg_obj, dict):
            return
        src = str(msg_obj.get("source", ""))
        try:
            if src == "礼物" and AppState.get_show_gift_print():
                nick = str(msg_obj.get("nickName", "") or "")
                gift = str(msg_obj.get("giftName", "") or "")
                if nick and gift:
                    value = msg_obj.get("price") or 0
                    try:
                        value = int(value)
                    except (TypeError, ValueError):
                        value = 0
                    is_big = (AppState.get_high_value_priority_print() and
                              value >= AppState.get_high_value_threshold())
                    self.print_mgr.print_event(
                        "礼物",
                        {"nick": nick, "gift": gift,
                         "count": msg_obj.get("giftCount", 1),
                         "value": value,
                         "avatar": msg_obj.get("avatar", "")},
                        dedup_key=(nick, gift),
                        priority=is_big,
                    )
            elif src == "聊天" and AppState.get_show_chat_print():
                nick = str(msg_obj.get("nickName", "") or "")
                text = str(msg_obj.get("msg", "") or "")
                if nick and text:
                    self.print_mgr.print_event(
                        "聊天", {"nick": nick, "text": text},
                        dedup_key=(nick, text),
                    )
            elif src == "点赞" and AppState.get_show_like_print():
                nick = str(msg_obj.get("nickName", "") or "")
                if nick:
                    self.print_mgr.print_event(
                        "点赞", {"nick": nick, "count": msg_obj.get("goodCount", 1)},
                        dedup_key=(nick,),
                    )
            elif src == "进场" and AppState.get_show_enter_print():
                nick = str(msg_obj.get("nickName", "") or "")
                if nick:
                    self.print_mgr.print_event(
                        "进场", {"nick": nick}, dedup_key=(nick,),
                    )
            elif src == "关注" and AppState.get_show_follow_print():
                nick = str(msg_obj.get("nickName", "") or "")
                if nick:
                    self.print_mgr.print_event(
                        "关注", {"nick": nick}, dedup_key=(nick,),
                    )
            elif src == "粉丝" and AppState.get_show_fans_print():
                nick = str(msg_obj.get("nickName", "") or "")
                text = str(msg_obj.get("msg", "") or "")
                if nick:
                    self.print_mgr.print_event(
                        "粉丝团", {"nick": nick, "text": text},
                        dedup_key=(nick, text),
                    )
        except Exception as e:
            logging.warning("小票打印触发失败: %s", e)

    def closeEvent(self, event):
        self.save_runtime_config()
        # 关窗口 = 停服务：抓取器和对外 WS 都在本进程，必须显式停掉再销毁窗口。
        try:
            if self.stop_btn.isEnabled():      # 只有已启动过才需要停
                self.on_stop()
            else:
                self._stop_live_services()
        except Exception as e:
            logger.warning("关闭窗口时停止服务异常: %s", e)
        # 兜底：确保 8765 释放、所有外部 WS 客户端断开（_stop_live_services 可重复调用）
        try:
            self._stop_live_services()
        except Exception:
            pass
        for handler in logger.handlers:
            handler.close()
        event.accept()


if __name__ == '__main__':
    # 可选：不带 GUI 单独跑推送服务（命令行 python page/liveMain.py --internal-live-server ...）。
    # 正常使用时 GUI 会在自己进程内启动同样的组件，不拉子进程。
    if "--internal-live-server" in sys.argv:
        sys.argv.remove("--internal-live-server")
        from server.live_server import main as _live_server_main
        _live_server_main()
        sys.exit(0)

    app = QApplication(sys.argv)
    window = MyWindow()
    window.show()
    sys.exit(app.exec_())
    
