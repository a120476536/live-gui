#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""从本机浏览器自动提取抖音登录态 Cookie，替代油猴子脚本手动复制粘贴。

用途：live-gui 的「抖音Cookie」输入框可一键填入浏览器里已登录的完整 Cookie。
抖音从 2026-04 起对 WebcastGiftMessage 做风控，只有登录态 Cookie 才会收到礼物帧；
从直播间页面复制的游客 Cookie 只能收聊天/点赞/进场。

注意：
  - 只在本机读取浏览器 Cookie，绝不打印 Cookie 值、不写日志、不外传。
  - Chrome 127+ / 新版 Edge 启用了 App-Bound Encryption，第三方进程无法解密，
    browser_cookie3 会报 "Unable to get key for cookie decryption"；此时会自动
    尝试其它浏览器（Firefox 的 Cookie 库是明文 SQLite，最稳）。
  - 工具只能自动「复制」，不能自动「登录」：必须先在浏览器登录 www.douyin.com。
"""
import logging

# 与 server/WebSocketServerD.py 的 DouyinLiveWebFetcher.LOGIN_COOKIE_KEYS 保持一致
LOGIN_COOKIE_KEYS = (
    "sessionid", "sessionid_ss", "sid_tt", "sid_guard",
    "uid_tt", "uid_tt_ss", "passport_auth_status",
)

# 尝试顺序：本机实测 Edge(120) 可读、Chrome(157) 被 ABE 拦截，故 Edge 优先。
BROWSERS = ("edge", "chrome", "firefox", "brave", "opera", "vivaldi",
            "chromium", "librewolf", "chromium_edge")


class CookieImportError(Exception):
    """所有浏览器都提取失败，或提取到的都不是登录态 Cookie。"""


def _domain_match(domain: str, host: str) -> bool:
    """复现浏览器 Cookie 的 domain 匹配（等价于油猴子脚本里的 domainMatch）。"""
    d = (domain or "").lower().lstrip(".")
    h = (host or "").lower()
    if not d or not h:
        return True
    return h == d or h.endswith("." + d)


def _path_match(cookie_path: str, req_path: str) -> bool:
    """复现浏览器 Cookie 的 path 匹配（等价于油猴子脚本里的 pathMatch）。"""
    p = cookie_path or "/"
    r = req_path or "/"
    if p == "/" or r == p:
        return True
    return r.startswith(p if p.endswith("/") else p + "/")


def _to_header(cookies) -> str:
    """按浏览器实际会携带的范围过滤 + 去重，拼成 Cookie 请求头。"""
    selected = [c for c in cookies
                if _domain_match(getattr(c, "domain", ""), "live.douyin.com")
                and _path_match(getattr(c, "path", "/"), "/")
                and getattr(c, "value", None)]
    merged = {}
    for c in selected:
        merged[c.name] = c.value
    return "; ".join("%s=%s" % (k, v) for k, v in merged.items()), len(merged)


def extract_douyin_cookie():
    """自动提取抖音登录态 Cookie。

    :return: dict(cookie, browser, fields, login_keys, errors)
    :raises CookieImportError: 没有可用浏览器 / 没有登录态 Cookie
    """
    try:
        import browser_cookie3 as bc3
    except ImportError as exc:
        raise CookieImportError(
            "未安装 browser_cookie3，请执行 pip install browser-cookie3（%s）" % exc)

    errors = {}

    for name in BROWSERS:
        loader = getattr(bc3, name, None)
        if loader is None:
            continue
        try:
            jar = loader(domain_name="douyin.com")
            header, count = _to_header(list(jar))
        except Exception as exc:
            errors[name] = "%s: %s" % (type(exc).__name__, str(exc)[:120])
            continue

        if not count:
            errors[name] = "没有匹配到 douyin.com 的 Cookie"
            continue

        names = [p.split("=", 1)[0].strip() for p in header.split(";") if "=" in p]
        hits = [k for k in LOGIN_COOKIE_KEYS if k in names]
        result = {
            "cookie": header,
            "browser": name,
            "fields": count,
            "login_keys": hits,
            "errors": errors,
        }
        if hits:
            # 只记录字段名，绝不记录 Cookie 值
            logging.info("自动提取抖音 Cookie：浏览器=%s 字段=%d 登录凭证=%s",
                         name, count, ",".join(hits))
            return result
        errors[name] = "Cookie 可读但为游客态（未登录 www.douyin.com）"

    detail = "；".join("%s -> %s" % (k, v) for k, v in errors.items()) or "未检测到受支持的浏览器"
    if "Unable to get key" in detail:
        detail += "（Chrome/Edge 127+ 的 App-Bound Encryption 无法解密，请改用 Firefox 或在旧版 Edge 登录）"
    raise CookieImportError(
        "没有提取到「登录态」抖音 Cookie。请先在浏览器登录 www.douyin.com 后重试。\n"
        "各浏览器结果：%s" % detail)
