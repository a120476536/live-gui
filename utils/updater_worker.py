#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, sys, json, time, shutil, tempfile, zipfile, requests, subprocess, logging
# 让项目根目录进入 sys.path：本文件作为独立脚本运行（`python utils/updater_worker.py`）
# 时，Python 只把 utils/ 加进 sys.path，无法解析 `from core.xxx` 包导入，须手动补根目录。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pathlib import Path
from PyQt5.QtWidgets import (QApplication, QWidget, QVBoxLayout, QLabel,
                             QProgressBar, QMessageBox)
from PyQt5.QtCore import QThread, pyqtSignal, Qt
from core.app_state import AppState

log_file = Path(tempfile.gettempdir()) / "updater_worker.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(log_file, encoding="utf-8")],
)

cfg_path = Path(tempfile.gettempdir()) / "liveMain_update.json"
with open(cfg_path, encoding="utf-8") as f:
    cfg = json.load(f)

url      = cfg["download_url"]
app_dir  = Path(cfg["target_dir"])
exe_name = cfg["exe_name"]
exe_path = app_dir / exe_name
zip_path = Path(tempfile.gettempdir()) / "liveMain.zip"

class Worker(QThread):
    progress = pyqtSignal(int)
    message  = pyqtSignal(str)
    finished = pyqtSignal(bool)

    def run(self):
        try:
            # 1) 下载
            self.message.emit("下载中…")
            logging.info("开始下载")
            with requests.get(url, stream=True, timeout=60) as r:
                r.raise_for_status()
                total = int(r.headers.get("Content-Length", 0)) or 1
                down  = 0
                with open(zip_path, "wb") as f:
                    for chunk in r.iter_content(1024 * 512):
                        if chunk:
                            f.write(chunk)
                            down += len(chunk)
                            self.progress.emit(int(down * 100 / total))
            logging.info("下载完成")

            # 2) 解压到临时目录（只包含 liveMain.exe）
            # temp_dir = Path(tempfile.gettempdir()) / "liveMain_exe"
            temp_dir = app_dir.parent / f"__update_{int(time.time())}"
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
            temp_dir.mkdir()

            with zipfile.ZipFile(zip_path, "r") as z:
                z.extractall(temp_dir)

            new_exe = next(temp_dir.rglob("liveMain.exe"), None)
            if new_exe is None:
                raise FileNotFoundError("压缩包内缺少 liveMain.exe")

            # 3) 杀进程（只关主程序即可）
            subprocess.run(
                ["taskkill", "/F", "/IM", exe_name],
                capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW
            )
            time.sleep(1)

            # 4) 原子替换 exe
            backup_exe = exe_path.with_suffix(".bak")
            if backup_exe.exists():
                backup_exe.unlink()
            exe_path.rename(backup_exe)   # 备份旧 exe
            new_exe.replace(exe_path)     # 覆盖
            logging.info("liveMain.exe 已替换")

            # 5) 重启
            subprocess.Popen([exe_path], cwd=app_dir)
            self.finished.emit(True)
            logging.info("更新完成并已重启")

        except Exception as e:
            logging.exception(e)
            self.message.emit(str(e))
            self.finished.emit(False)

class UpdaterWindow(QWidget):
    def __init__(self):
        super().__init__()
        # self.setWindowTitle("程序更新")
        self.setWindowTitle(AppState.live_title+"更新")
        self.resize(320, 120)
        self.setFixedSize(self.size())
        self.setWindowFlags(Qt.WindowStaysOnTopHint)

        self.label = QLabel("准备更新…")
        self.bar   = QProgressBar()
        lay = QVBoxLayout(self)
        lay.addWidget(self.label)
        lay.addWidget(self.bar)

        self.worker = Worker()
        self.worker.progress.connect(self.bar.setValue)
        self.worker.message.connect(self.label.setText)
        self.worker.finished.connect(self.on_done)
        self.worker.start()

    def on_done(self, ok):
        if ok:
            QMessageBox.information(self, "完成", "更新成功，程序即将启动。")
        else:
            QMessageBox.warning(self, "失败", f"更新失败！\n请查看日志：{log_file}")
        self.close()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = UpdaterWindow()
    w.show()
    sys.exit(app.exec_())