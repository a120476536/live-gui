#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""控制台 / logging 编码兜底（Windows GBK 控制台 + PyInstaller -w 无控制台）。

问题：WebSocketServerD / SerialManager / DyHttpServer 等模块在「导入期」就调用
logging.basicConfig()，创建的 StreamHandler 绑定当时的 sys.stderr。中文 Windows 控制台
默认 gbk，写 emoji（✓ ✨ 𝕳 …）会抛 UnicodeEncodeError，logging 于是打印整段
「--- Logging error ---」traceback 刷屏。PyInstaller -w 打包时 sys.stdout/stderr 为 None，
print / http.server.log_message 还会抛 AttributeError。

用法：在进程最早的入口（业务模块 import 之前）调用 install_safe_console()；可重复调用。
  1. 把 sys.stdout / sys.stderr 包成「写不崩」的流（编码失败自动替换为 ?）；
  2. 把已经创建的 logging StreamHandler 全部重新指向安全流；
  3. 若 root logger 还没有 handler，预装一个安全 handler，让后续各模块的
     basicConfig() 变成 no-op，不再偷偷创建 gbk 的 handler。
"""
import io
import logging
import os
import sys


class _SafeStream:
    """代理任意文本流：编码失败时降级为可编码字符，写入永不抛异常。"""

    def __init__(self, raw):
        self._raw = raw

    def write(self, s):
        if s is None:
            return 0
        try:
            return self._raw.write(s)
        except UnicodeEncodeError:
            enc = getattr(self._raw, "encoding", None) or "utf-8"
            try:
                return self._raw.write(s.encode(enc, "replace").decode(enc, "replace"))
            except Exception:
                return len(s)
        except Exception:
            return len(s)

    def flush(self):
        try:
            self._raw.flush()
        except Exception:
            pass

    def isatty(self):
        try:
            return bool(self._raw.isatty())
        except Exception:
            return False

    def __getattr__(self, name):
        return getattr(self._raw, name)


class _FileStream:
    """sys.stdout/stderr 为 None（PyInstaller -w）时，写 UTF-8 文件兜底。"""

    def __init__(self, path):
        self.encoding = "utf-8"
        self._fh = None
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            self._fh = open(path, "a", encoding="utf-8", buffering=1)
        except Exception:
            pass

    def write(self, s):
        if self._fh is not None:
            try:
                return self._fh.write(s)
            except Exception:
                pass
        return len(s)

    def flush(self):
        if self._fh is not None:
            try:
                self._fh.flush()
            except Exception:
                pass

    def isatty(self):
        return False


def install_safe_console():
    """幂等安装；返回 True 表示本次确实做了安装/重定向。"""
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    changed = False
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if isinstance(stream, _SafeStream):
            continue
        if stream is None:
            kind = "out" if name == "stdout" else "err"
            stream = _FileStream(os.path.join(base, "log", "std_%s.log" % kind))
        else:
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                buf = getattr(stream, "buffer", None)
                if buf is not None:
                    try:
                        stream = io.TextIOWrapper(buf, encoding="utf-8",
                                                  errors="replace", line_buffering=True)
                    except Exception:
                        pass
        setattr(sys, name, _SafeStream(stream))
        changed = True

    root = logging.getLogger()
    safe = sys.stderr
    seen = set()
    loggers = [root] + [x for x in logging.Logger.manager.loggerDict.values()
                        if isinstance(x, logging.Logger)]
    for lg in loggers:
        for h in list(getattr(lg, "handlers", [])):
            if id(h) in seen:
                continue
            seen.add(id(h))
            # FileHandler 是 StreamHandler 子类，但有 baseFilename，不能改它的流
            if isinstance(h, logging.StreamHandler) and not hasattr(h, "baseFilename"):
                try:
                    h.setStream(safe)
                except Exception:
                    try:
                        h.stream = safe
                    except Exception:
                        pass
    if not root.handlers:
        h = logging.StreamHandler(safe)
        h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        root.addHandler(h)
        root.setLevel(logging.INFO)
        changed = True
    return changed
