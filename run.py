#!/usr/bin/env python3
# 项目统一入口（开发态）。
# 用法：在项目根目录执行  python run.py
# 根目录已加入 sys.path，内部模块均按包导入（server / core / utils / widget / page）。
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt5.QtWidgets import QApplication
from page.liveMain import MyWindow

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MyWindow()
    window.show()
    sys.exit(app.exec_())
