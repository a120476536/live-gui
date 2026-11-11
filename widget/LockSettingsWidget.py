# LockSettingsWidget.py
import json, os, sys
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (QWidget,QDialog,QVBoxLayout, QHBoxLayout, QLabel,
                             QLineEdit, QPushButton, QSpinBox, QMessageBox,
                             QScrollArea, QGroupBox, QFrame)

from utils.SerialManager import serial_manager
import logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
# 获取当前程序目录
if getattr(sys, 'frozen', False):
    # 打包后的可执行文件运行
    base_dir = os.path.dirname(sys.executable)
else:
    # 开发环境中运行
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    
class LockRow(QWidget):
    """单个锁口配置行"""
    def __init__(self, idx, parent=None):
        super().__init__(parent)
        self.idx = idx
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)

        h.addWidget(QLabel(f"线路{idx + 1}"))

        self.addr = QLineEdit("1")
        self.addr.setFixedWidth(50)
        h.addWidget(QLabel("地址:"))
        h.addWidget(self.addr)

        self.lock_no = QLineEdit(f"{idx + 1}")
        self.lock_no.setFixedWidth(50)
        h.addWidget(QLabel("锁号:"))
        h.addWidget(self.lock_no)

        self.gift_name = QLineEdit("")
        self.gift_name.setFixedWidth(120)
        h.addWidget(QLabel("礼物名:"))
        h.addWidget(self.gift_name)

        self.btn_test = QPushButton("测试")
        self.btn_test.clicked.connect(self.test_open)
        h.addWidget(self.btn_test)

    # ---------------- 功能 ----------------
    def test_open(self):
        addr = self.addr.text().strip()
        lock = self.lock_no.text().strip()
        gift_name = self.gift_name.text().strip()
        if not addr.isdigit() or not lock.isdigit():
            QMessageBox.warning(self, "提示", "地址和锁号必须是数字！")
            return
        if not serial_manager.is_open():
            QMessageBox.warning(self, "提示", "串口未连接！")
            return
        if addr and lock and gift_name:
            cmd = f"55{int(addr):02X}A1{int(lock):02X}00"
            serial_manager.send_command(cmd)
            QMessageBox.information(self, "测试", f"已发送：{cmd.strip()}")
        else:
            QMessageBox.warning(self, "提示", "地址、锁号礼物不能为空！")

    def get_cfg(self):
        return {
            "addr": self.addr.text().strip(),
            "lock": self.lock_no.text().strip(),
            "giftName": self.gift_name.text().strip()
        }

    def set_cfg(self, cfg):
        self.addr.setText(cfg.get("addr", "1"))
        self.lock_no.setText(cfg.get("lock", "1"))
        self.gift_name.setText(cfg.get("giftName", ""))
def cfg_path():
    """返回 lock_cfg.json 的绝对路径（兼容开发/打包）"""
    lock_cfg_path = os.path.join(base_dir, "data")
    os.makedirs(lock_cfg_path, exist_ok=True)  # 如果不存在就创建
    lock_cfg_file = os.path.join(lock_cfg_path, "lock_cfg.json")
    return lock_cfg_file

class LockSettingsWidget(QDialog):
    settings_saved = pyqtSignal(dict)   # 通知主窗口

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("锁控板配置")
        self.resize(600, 400)
        self.setWindowModality(Qt.ApplicationModal)

        self.cfg_file = cfg_path()
        self.rows = []          # 存放 LockRow 实例
        self.init_ui()
        self._first_load = True
        self.load_cfg()
        

    # ---------- UI ----------
    def init_ui(self):
        top = QVBoxLayout(self)

        # 1. 数量控制
        h = QHBoxLayout()
        h.addWidget(QLabel("锁口数量:"))
        self.sp_count = QSpinBox()
        self.sp_count.setRange(1, 32)
        self.sp_count.setValue(8)
        self.sp_count.valueChanged.connect(self.change_count)
        h.addWidget(self.sp_count)
        h.addStretch()
        top.addLayout(h)

        # 2. 可滚动区域
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.scroll_widget = QWidget()
        self.scroll_layout = QVBoxLayout(self.scroll_widget)
        self.scroll_layout.setAlignment(Qt.AlignTop)
        scroll.setWidget(self.scroll_widget)
        top.addWidget(scroll)

        # 3. 保存按钮
        self.btn_save = QPushButton("保存配置")
        self.btn_save.clicked.connect(self.save_cfg)
        top.addWidget(self.btn_save)

    # ---------- 锁口数量变更 ----------
    def change_count(self, n):
        """锁口数量变化：重建行并回填现有数据"""
        # 记录当前所有数据
        old_cfg = [row.get_cfg() for row in self.rows]

        # 清空界面
        for row in self.rows:
            row.setParent(None)
        self.rows.clear()

        # 重建 n 行
        for i in range(n):
            row = LockRow(i)
            self.scroll_layout.addWidget(row)
            self.rows.append(row)

        # 回填旧数据（超出的部分留空）
        for idx, row in enumerate(self.rows):
            if idx < len(old_cfg):
                row.set_cfg(old_cfg[idx])
    
    
    
    

    # ---------- 配置读写 ----------
    def load_cfg(self):
        """第一次打开时读取 json"""
        try:
            with open(self.cfg_file, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except FileNotFoundError:
            cfg = {"count": 8, "list": []}

        count = cfg.get("count", 8)
        self.sp_count.blockSignals(True)
        self.sp_count.setValue(count)
        self.sp_count.blockSignals(False)

        # 重建 + 回填
        self.change_count(count)
        for idx, row in enumerate(self.rows):
            if idx < len(cfg.get("list", [])):
                row.set_cfg(cfg["list"][idx])
    
    
    
                

    def save_cfg(self):
        cfg_list = [row.get_cfg() for row in self.rows]
        # 检查礼物名是否全为空
        if all(item.get("giftName", "").strip() == "" for item in cfg_list):
            QMessageBox.warning(self, "提示", "所有礼物名不能为空，请填写后再保存！")
            return
        # 保存的json的时候 重构一下 直接把指令写进去
        for item in cfg_list:
            addr = int(item["addr"])
            lock = int(item["lock"])
            item["cmd"] = f"55{addr:02X}A1{lock:02X}00"
        
        cfg = {"count": self.sp_count.value(), "list": cfg_list}
        try:
            with open(self.cfg_file, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception as e:
            QMessageBox.warning(self, "错误", f"保存失败：{e}")
            return

        self.settings_saved.emit(cfg)
        QMessageBox.information(self, "提示", "保存成功！")
        self.accept()   # 关闭窗口