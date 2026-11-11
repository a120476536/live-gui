# 这个先不用了 自身进程占用 无法释放 更新失败
# import os
# import sys
# import requests
# import zipfile
# import shutil
# import tempfile
# import logging
# from PyQt5.QtWidgets import QMessageBox, QProgressDialog
# from PyQt5.QtCore import QThread, pyqtSignal
# from app_state import AppState
# logging.basicConfig(
#     level=logging.DEBUG,
#     format="%(asctime)s [%(levelname)s] %(message)s"
# )

# UPDATE_INFO_URL = "http://video.zhengduimen.com/api.php/update/liveMain"


# # ---------------- 下载线程 ----------------
# class DownloadThread(QThread):
#     progress = pyqtSignal(int)      # 下载进度 %
#     finished = pyqtSignal(str)      # 下载完成（返回文件路径）
#     error = pyqtSignal(str)         # 出错

#     def __init__(self, url, save_dir):
#         super().__init__()
#         self.url = url
#         self.save_dir = save_dir

#     def run(self):
#         try:
#             local_file = os.path.join(self.save_dir, os.path.basename(self.url))
#             logging.info(f"线程开始下载: {self.url}")

#             with requests.get(self.url, stream=True, timeout=(5, 30)) as r:
#                 r.raise_for_status()
#                 total = int(r.headers.get("Content-Length", 0))
#                 downloaded = 0

#                 with open(local_file, "wb") as f:
#                     for chunk in r.iter_content(1024 * 512):  # 512KB 一次
#                         if chunk:
#                             f.write(chunk)
#                             downloaded += len(chunk)
#                             percent = int(downloaded * 100 / total) if total else 0
#                             self.progress.emit(percent)

#             logging.info(f"下载完成: {local_file}")
#             self.finished.emit(local_file)

#         except Exception as e:
#             logging.error(f"下载出错: {e}", exc_info=True)
#             self.error.emit(str(e))


# # ---------------- 解压线程 ----------------
# class ExtractThread(QThread):
#     progress = pyqtSignal(int)      # 解压进度 %
#     finished = pyqtSignal()         # 解压完成
#     error = pyqtSignal(str)         # 出错

#     def __init__(self, zip_path, app_dir, backup_dir):
#         super().__init__()
#         self.zip_path = zip_path
#         self.app_dir = app_dir
#         self.backup_dir = backup_dir

#     def run(self):
#         try:
#             logging.info(f"解压线程启动: {self.zip_path}")

#             # 先备份
#             if os.path.exists(self.backup_dir):
#                 shutil.rmtree(self.backup_dir)
#             shutil.copytree(self.app_dir, self.backup_dir, dirs_exist_ok=True)
#             logging.info("备份完成")

#             with zipfile.ZipFile(self.zip_path, "r") as zip_ref:
#                 file_list = zip_ref.infolist()
#                 total = len(file_list)

#                 for i, file in enumerate(file_list):
#                     zip_ref.extract(file, self.app_dir)
#                     percent = int((i + 1) * 100 / total)
#                     self.progress.emit(percent)

#             logging.info("解压完成")
#             self.finished.emit()

#         except Exception as e:
#             logging.error(f"解压出错: {e}", exc_info=True)
#             self.error.emit(str(e))


# # ---------------- Updater 主类 ----------------
# class Updater:
#     def __init__(self, parent):
#         self.parent = parent
#         self.local_version = AppState.get_live_version
#         self.temp_dir = tempfile.gettempdir()
#         logging.info(f"初始化 Updater，本地版本: {self.local_version}, 临时目录: {self.temp_dir}")

#     def check_update(self):
#         """检查是否有新版本"""
#         try:
#             data = {"deviceFrom": "Andr0id_sYsteM", "deviceIds": "012082539"}
#             resp = requests.post(UPDATE_INFO_URL, json=data, timeout=5)
#             resp.raise_for_status()

#             result = resp.json().get("list", {})
#             latest_version = result.get("version")
#             download_url = result.get("updateUrl")
#             changelog = result.get("content", "")

#             if latest_version and self._compare_version(latest_version):
#                 return True, latest_version, download_url, changelog
#             return False, latest_version, None, None
#         except Exception as e:
#             logging.error(f"检查更新失败: {e}", exc_info=True)
#             return False, None, None, None

#     def _compare_version(self, latest_version):
#         def parse(v): return [int(x) for x in v.split(".")]
#         return parse(latest_version) > parse(self.local_version)

#     # ---------------- 下载 ----------------
#     def start_download(self, url):
#         self.progress_dialog = QProgressDialog("正在下载更新...", "取消", 0, 100, self.parent)
#         self.progress_dialog.setWindowTitle("下载更新")
#         self.progress_dialog.setWindowModality(True)
#         self.progress_dialog.show()

#         self.thread = DownloadThread(url, self.temp_dir)
#         self.thread.progress.connect(self.progress_dialog.setValue)
#         self.thread.finished.connect(self._download_finished)
#         self.thread.error.connect(self._download_error)

#         self.progress_dialog.canceled.connect(self.thread.terminate)
#         self.thread.start()

#     def _download_finished(self, file_path):
#         self.progress_dialog.close()
#         QMessageBox.information(self.parent, "下载完成", f"更新包已下载: {file_path}")
#         self.start_extract(file_path)

#     def _download_error(self, msg):
#         self.progress_dialog.close()
#         QMessageBox.critical(self.parent, "下载失败", f"错误: {msg}")

#     # ---------------- 解压 ----------------
#     def start_extract(self, zip_path):
#         app_dir = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(__file__)
#         backup_dir = os.path.join(self.temp_dir, "backup_app")

#         self.progress_dialog = QProgressDialog("正在解压更新...", "取消", 0, 100, self.parent)
#         self.progress_dialog.setWindowTitle("应用更新")
#         self.progress_dialog.setWindowModality(True)
#         self.progress_dialog.show()

#         self.thread = ExtractThread(zip_path, app_dir, backup_dir)
#         self.thread.progress.connect(self.progress_dialog.setValue)
#         self.thread.finished.connect(self._extract_finished)
#         self.thread.error.connect(self._extract_error)

#         self.progress_dialog.canceled.connect(self.thread.terminate)
#         self.thread.start()

#     def _extract_finished(self):
#         self.progress_dialog.close()
#         QMessageBox.information(self.parent, "更新完成", "已成功更新到新版本，请重启程序！")
#         self.restart_app()

#     def _extract_error(self, msg):
#         self.progress_dialog.close()
#         QMessageBox.critical(self.parent, "解压失败", f"错误: {msg}")

#     # ---------------- 重启 ----------------
#     def restart_app(self):
#         try:
#             python = sys.executable
#             logging.info(f"准备重启程序，执行: {python} {sys.argv}")
#             os.execl(python, python, *sys.argv)
#         except Exception as e:
#             logging.error(f"重启程序失败: {e}", exc_info=True)


import os
import sys
import time
import requests
import zipfile
import shutil
import tempfile
import logging
import subprocess
from PyQt5.QtWidgets import QMessageBox, QProgressDialog, QApplication
from PyQt5.QtCore import (QThread, pyqtSignal, QCoreApplication, Qt, 
                          QMetaObject, Q_ARG, QEvent)  # 关键修复：导入QEvent

# 初始化日志（确保目录存在）
def init_logging():
    log_dir = os.path.join(tempfile.gettempdir(), "updater_logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "updater_log.txt")
    
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.FileHandler(log_file)]
    )

init_logging()

UPDATE_INFO_URL = "http://pgcms.zhengduimen.com/api.php/update/liveMain"


def is_file_locked(file_path):
    if not os.path.exists(file_path):
        return False
    try:
        with open(file_path, 'a'):
            return False
    except (IOError, PermissionError):
        return True


def wait_for_file_unlock(file_path, max_wait=15):
    wait_time = 0
    while is_file_locked(file_path) and wait_time < max_wait:
        time.sleep(1)
        wait_time += 1
    return not is_file_locked(file_path)


class DownloadThread(QThread):
    progress = pyqtSignal(int)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, url, save_dir):
        super().__init__()
        self.url = url
        self.save_dir = save_dir
        self.is_running = True

    def run(self):
        try:
            update_filename = os.path.basename(self.url)
            local_zip = os.path.join(self.save_dir, update_filename)

            if os.path.exists(local_zip):
                try:
                    os.remove(local_zip)
                    logging.info(f"已删除旧更新包：{local_zip}")
                except PermissionError:
                    logging.warning(f"旧更新包被占用，将覆盖写入：{local_zip}")

            logging.info(f"开始下载更新包：{self.url}")
            with requests.get(self.url, stream=True, timeout=(5, 30)) as resp:
                resp.raise_for_status()
                total_size = int(resp.headers.get("Content-Length", 0)) or 1
                downloaded_size = 0

                with open(local_zip, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=1024 * 256):
                        if not self.is_running:
                            logging.info("下载线程已终止")
                            return
                        if chunk:
                            f.write(chunk)
                            downloaded_size += len(chunk)
                            progress = min(int(downloaded_size * 100 / total_size), 100)
                            self.progress.emit(progress)

            if os.path.getsize(local_zip) < 1024:
                raise Exception("更新包为空，可能下载不完整")

            logging.info(f"下载完成：{local_zip}")
            self.finished.emit(local_zip)

        except Exception as e:
            error_msg = str(e)
            logging.error(f"下载失败：{error_msg}")
            self.error.emit(error_msg)

    def terminate(self):
        self.is_running = False
        super().terminate()


class ExtractThread(QThread):
    progress = pyqtSignal(int)
    finished = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, zip_path, app_dir, backup_dir, exe_name="liveMain.exe"):
        super().__init__()
        self.zip_path = zip_path
        self.app_dir = app_dir
        self.backup_dir = backup_dir
        self.exe_name = exe_name
        self.is_running = True

    def run(self):
        try:
            if not os.path.exists(self.zip_path):
                raise Exception(f"更新包不存在：{self.zip_path}")
            if not os.path.exists(self.app_dir):
                raise Exception(f"程序目录不存在：{self.app_dir}")

            logging.info(f"开始解压更新包：{self.zip_path} -> {self.app_dir}")
            self._backup_core_files()
            logging.info("核心文件备份完成")

            # 强制终止主程序（增加重试机制）
            exe_full_path = os.path.join(self.app_dir, self.exe_name)
            if os.path.exists(exe_full_path):
                if self._is_process_running(self.exe_name):
                    logging.warning(f"检测到主程序 {self.exe_name} 正在运行，尝试终止")
                    # 最多重试3次终止进程
                    for _ in range(3):
                        if self._kill_process(self.exe_name):
                            break
                        time.sleep(1)
                    if self._is_process_running(self.exe_name):
                        raise Exception(f"主程序无法自动关闭，请手动关闭 {self.exe_name} 后重试")
                # 等待进程完全退出
                time.sleep(3)

            with zipfile.ZipFile(self.zip_path, "r") as zip_ref:
                file_list = zip_ref.infolist()
                total_files = len(file_list) or 1

                root_dirs = set()
                for file_info in file_list:
                    first_dir = file_info.filename.split("/")[0].strip()
                    if first_dir and not first_dir.startswith("."):
                        root_dirs.add(first_dir)

                has_single_root = len(root_dirs) == 1
                target_root = root_dirs.pop() if has_single_root else ""
                logging.info(f"压缩包结构：{'含唯一根目录：' + target_root if has_single_root else '无统一根目录'}")

                for idx, file_info in enumerate(file_list):
                    if not self.is_running:
                        logging.info("解压线程已终止")
                        return

                    if has_single_root and target_root:
                        if file_info.filename.startswith(f"{target_root}/"):
                            relative_path = file_info.filename[len(f"{target_root}/"):]
                        else:
                            relative_path = file_info.filename
                    else:
                        relative_path = file_info.filename

                    target_path = os.path.join(self.app_dir, relative_path)

                    if file_info.is_dir():
                        if not os.path.exists(target_path):
                            os.makedirs(target_path, exist_ok=True)
                        continue

                    target_dir = os.path.dirname(target_path)
                    if not os.path.exists(target_dir):
                        os.makedirs(target_dir, exist_ok=True)

                    # 强制删除旧文件（增加解锁等待）
                    if os.path.exists(target_path):
                        if not wait_for_file_unlock(target_path, 10):
                            logging.warning(f"文件 {target_path} 始终被占用，尝试强制删除")
                            try:
                                # 强制删除只读文件
                                os.chmod(target_path, 0o777)
                                os.remove(target_path)
                            except PermissionError:
                                logging.warning(f"无法删除 {target_path}，跳过覆盖")
                                continue
                        else:
                            try:
                                os.remove(target_path)
                            except PermissionError:
                                logging.warning(f"删除 {target_path} 失败，跳过覆盖")
                                continue

                    try:
                        with zip_ref.open(file_info) as src, open(target_path, "wb") as dst:
                            shutil.copyfileobj(src, dst, 1024 * 128)
                        # 设置文件权限为可执行
                        os.chmod(target_path, 0o755)
                    except PermissionError:
                        logging.warning(f"无权限写入 {target_path}，跳过该文件")
                        continue

                    if idx % 5 == 0 or idx == total_files - 1:
                        progress = min(int((idx + 1) * 100 / total_files), 100)
                        self.progress.emit(progress)

            logging.info("解压完成，所有文件已部署到程序目录")
            self.finished.emit()

        except Exception as e:
            error_msg = str(e)
            logging.error(f"解压失败：{error_msg}")
            self._rollback_core_files()
            self.error.emit(error_msg)

    def _backup_core_files(self):
        if not os.path.exists(self.backup_dir):
            os.makedirs(self.backup_dir, exist_ok=True)

        core_files = [
            self.exe_name,
            "app_state.py",
            "config.ini"
        ]

        for file_name in core_files:
            src_path = os.path.join(self.app_dir, file_name)
            dst_path = os.path.join(self.backup_dir, file_name)
            if os.path.exists(src_path):
                try:
                    shutil.copy2(src_path, dst_path)
                    # 备份权限
                    shutil.copystat(src_path, dst_path)
                    logging.info(f"已备份：{file_name}")
                except Exception as e:
                    logging.warning(f"备份 {file_name} 失败：{str(e)}")

    def _rollback_core_files(self):
        if not os.path.exists(self.backup_dir):
            return

        core_files = [self.exe_name, "app_state.py", "config.ini"]
        for file_name in core_files:
            src_path = os.path.join(self.backup_dir, file_name)
            dst_path = os.path.join(self.app_dir, file_name)
            if os.path.exists(src_path):
                try:
                    if os.path.exists(dst_path):
                        os.remove(dst_path)
                    shutil.copy2(src_path, dst_path)
                    shutil.copystat(src_path, dst_path)
                    logging.info(f"已回滚：{file_name}")
                except Exception as e:
                    logging.warning(f"回滚 {file_name} 失败：{str(e)}")

    def _is_process_running(self, process_name):
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {process_name}", "/FO", "CSV"],
                capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW
            )
            return process_name in result.stdout and len(result.stdout.splitlines()) > 1
        except Exception as e:
            logging.error(f"检测进程失败：{e}")
            return False

    def _kill_process(self, process_name):
        try:
            # 先尝试正常关闭
            subprocess.run(
                ["taskkill", "/IM", process_name],
                capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW
            )
            time.sleep(1)
            # 如果还在运行，强制关闭
            if self._is_process_running(process_name):
                subprocess.run(
                    ["taskkill", "/F", "/IM", process_name],
                    capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW
                )
            time.sleep(1)
            return not self._is_process_running(process_name)
        except Exception as e:
            logging.error(f"终止进程失败：{e}")
            return False

    def terminate(self):
        self.is_running = False
        super().terminate()


class Updater:
    def __init__(self, parent_window):
        self.parent = parent_window or QApplication.activeWindow()
        self.local_version = self._get_safe_version()
        self.temp_dir = tempfile.gettempdir()
        self.app_dir = self._get_app_directory()
        self.backup_dir = os.path.join(self.temp_dir, "liveMain_backup")
        self.exe_name = "liveMain.exe"
        self.exe_path = os.path.join(self.app_dir, self.exe_name)
        
        # 线程和对话框引用
        self.download_thread = None
        self.extract_thread = None
        self.download_dialog = None
        self.extract_dialog = None

        logging.info(f"更新器初始化完成：")
        logging.info(f"  本地版本：{self.local_version}")
        logging.info(f"  程序目录：{self.app_dir}")
        logging.info(f"  程序路径：{self.exe_path}")
        logging.info(f"  临时目录：{self.temp_dir}")

    def _get_safe_version(self):
        try:
            from core.app_state import AppState
            version = AppState.get_live_version()
            return version if self._is_valid_version(version) else "1.0.0"
        except Exception as e:
            logging.warning(f"获取本地版本失败：{str(e)}，使用默认版本 1.0.0")
            return "1.0.0"

    def _get_app_directory(self):
        try:
            if getattr(sys, 'frozen', False):
                return os.path.dirname(os.path.abspath(sys.executable))
            else:
                return os.path.dirname(os.path.abspath(__file__))
        except Exception:
            return os.getcwd()

    def _is_valid_version(self, version_str):
        if not isinstance(version_str, str):
            return False
        parts = version_str.split(".")
        return len(parts) == 3 and all(part.isdigit() for part in parts)

    def check_update(self):
        try:
            request_data = {"deviceFrom": "Andr0id_sYsteM", "deviceIds": "012082539"}
            resp = requests.post(UPDATE_INFO_URL, json=request_data, timeout=8)
            resp.raise_for_status()

            resp_json = resp.json()
            update_info = resp_json.get("list", {})
            latest_version = update_info.get("version", "")
            download_url = update_info.get("updateUrl", "")
            changelog = update_info.get("content", "无详细更新日志")

            if self._is_valid_version(latest_version) and self._version_gt(latest_version, self.local_version):
                logging.info(f"检测到新版本：{latest_version}（当前：{self.local_version}）")
                # 确保在主线程显示更新提示
                QApplication.postEvent(
                    self.parent, 
                    UpdateAvailableEvent(latest_version, download_url, changelog)
                )
                return True, latest_version, download_url, changelog
            else:
                logging.info(f"当前已是最新版本：{self.local_version}（最新：{latest_version}）")
                return False, latest_version, None, None

        except Exception as e:
            error_msg = str(e)
            logging.error(f"检查更新失败：{error_msg}")
            return False, None, None, f"检查更新失败：{error_msg}"

    def _version_gt(self, ver1, ver2):
        try:
            ver1_parts = list(map(int, ver1.split(".")))
            ver2_parts = list(map(int, ver2.split(".")))
            return ver1_parts > ver2_parts
        except Exception:
            return False

    def start_download(self, download_url):
        if not self.parent or self.parent.isHidden():
            logging.warning("父窗口不存在或已隐藏，无法显示下载进度")
            return

        if self.download_dialog and self.download_dialog.isVisible():
            self.download_dialog.close()

        self.download_dialog = QProgressDialog(
            "正在下载更新包...", "取消", 0, 100, self.parent
        )
        self.download_dialog.setWindowTitle("下载更新")
        self.download_dialog.setWindowModality(Qt.WindowModal)
        self.download_dialog.setMinimumDuration(1000)
        self.download_dialog.show()

        self.download_thread = DownloadThread(download_url, self.temp_dir)
        self.download_thread.progress.connect(self.download_dialog.setValue)
        self.download_thread.finished.connect(self._on_download_finished)
        self.download_thread.error.connect(self._on_download_error)
        self.download_dialog.canceled.connect(self._on_download_canceled)
        
        self.download_thread.start()

    def _on_download_finished(self, file_path):
        if self.download_dialog:
            self.download_dialog.close()
        
        # 确保在主线程执行
        QMetaObject.invokeMethod(
            self.parent, 
            "showUpdateReadyDialog",
            Q_ARG(str, file_path)
        )

    def _on_download_error(self, msg):
        if self.download_dialog:
            self.download_dialog.close()
        QMessageBox.critical(self.parent, "下载失败", f"错误: {msg}")

    def _on_download_canceled(self):
        if self.download_thread:
            self.download_thread.terminate()
            self.download_thread.wait()

    def start_extract(self, zip_path):
        """开始解压更新包并替换程序文件"""
        if not self.parent or self.parent.isHidden():
            logging.warning("父窗口不存在，无法显示解压进度")
            return

        if self.extract_dialog and self.extract_dialog.isVisible():
            self.extract_dialog.close()

        self.extract_dialog = QProgressDialog(
            "正在安装更新...", "取消", 0, 100, self.parent
        )
        self.extract_dialog.setWindowTitle("安装更新")
        self.extract_dialog.setWindowModality(Qt.WindowModal)
        self.extract_dialog.setMinimumDuration(1000)
        self.extract_dialog.show()

        self.extract_thread = ExtractThread(zip_path, self.app_dir, self.backup_dir, self.exe_name)
        self.extract_thread.progress.connect(self.extract_dialog.setValue)
        self.extract_thread.finished.connect(self._on_extract_finished)
        self.extract_thread.error.connect(self._on_extract_error)
        self.extract_dialog.canceled.connect(self._on_extract_canceled)
        
        self.extract_thread.start()

    def _on_extract_finished(self):
        """解压完成后重启程序"""
        if self.extract_dialog:
            self.extract_dialog.close()
        
        logging.info("更新安装完成，准备重启程序")
        # 确保在主线程执行重启
        QMetaObject.invokeMethod(self.parent, "restart_application")

    def _on_extract_error(self, msg):
        if self.extract_dialog:
            self.extract_dialog.close()
        QMessageBox.critical(self.parent, "更新失败", f"错误: {msg}\n已恢复到更新前版本")

    def _on_extract_canceled(self):
        if self.extract_thread:
            self.extract_thread.terminate()
            self.extract_thread.wait()

    def restart_application(self):
        """重启应用程序"""
        try:
            # 验证新程序是否存在
            if not os.path.exists(self.exe_path):
                raise Exception(f"程序文件不存在：{self.exe_path}")
            
            # 记录重启日志
            logging.info(f"准备重启程序：{self.exe_path}")
            
            # 启动新进程
            subprocess.Popen([self.exe_path], cwd=self.app_dir)
            
            # 等待新进程启动
            time.sleep(2)
            
            # 退出当前进程
            logging.info("更新完成，退出当前程序")
            QCoreApplication.quit()
            
        except Exception as e:
            error_msg = str(e)
            logging.error(f"重启程序失败：{error_msg}")
            QMessageBox.warning(
                self.parent, 
                "重启失败", 
                f"更新已完成，但自动重启失败，请手动启动程序：\n{self.exe_path}"
            )
            QCoreApplication.quit()


class UpdateAvailableEvent(QEvent):
    EVENT_TYPE = QEvent.Type(QEvent.registerEventType())
    
    def __init__(self, version, url, changelog):
        super().__init__(self.EVENT_TYPE)
        self.version = version
        self.url = url
        self.changelog = changelog


# 主窗口必须实现的方法（添加到你的主窗口类中）
"""
def event(self, event):
    if event.type() == UpdateAvailableEvent.EVENT_TYPE:
        self.on_update_available(event)
        return True
    return super().event(event)

def on_update_available(self, event):
    reply = QMessageBox.question(
        self, "发现新版本", 
        f"检测到新版本 {event.version}，是否更新？\n\n更新日志：{event.changelog}",
        QMessageBox.Yes | QMessageBox.No
    )
    if reply == QMessageBox.Yes:
        self.updater.start_download(event.url)

def showUpdateReadyDialog(self, file_path):
    reply = QMessageBox.question(
        self, "下载完成", 
        "更新包已下载完成，是否立即安装？（安装过程中程序将重启）",
        QMessageBox.Yes | QMessageBox.No
    )
    if reply == QMessageBox.Yes:
        self.updater.start_extract(file_path)

def restart_application(self):
    # 调用更新器的重启方法
    self.updater.restart_application()
"""
