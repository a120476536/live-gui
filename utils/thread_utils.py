# thread_utils.py
from PyQt5.QtCore import QObject, QThread, pyqtSignal

class OneShotWorker(QObject):
    finished = pyqtSignal()

    def __init__(self, func, *args, **kwargs):
        super().__init__()
        self._func = func
        self._args = args
        self._kwargs = kwargs

    def run(self):
        try:
            self._func(*self._args, **self._kwargs)
        finally:
            self.finished.emit()

def run_in_thread(func, *args, **kwargs):
    """一行调用：run_in_thread(fetcher.start)"""
    thread = QThread()
    worker = OneShotWorker(func, *args, **kwargs)
    worker.moveToThread(thread)
    
    thread.started.connect(worker.run)
    worker.finished.connect(thread.quit)
    thread.finished.connect(thread.deleteLater)
    thread.finished.connect(worker.deleteLater)

    thread.start()
    return thread      # 如果想手动 stop，可保留引用