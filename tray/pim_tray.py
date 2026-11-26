#!/usr/bin/env python3
"""pim 托盘客户端 —— 接收 IM 桌面容器的消息推送, 以系统托盘常驻。

Windows / macOS 通用:
- 连接 pim 服务端 SSE(/notify/events), 实时消息弹系统通知 + 未读角标
- backlog(历史消息)只累计未读, 不弹窗, 与网页端行为一致
- 托盘图标: 绿=已连接, 红=断线(自动重连), 灰=提醒已暂停; 右上角红色数字角标

依赖: pip install -r requirements.txt
配置: 同目录 config.json (首次运行自动生成), 例如:
    {"server": "https://192.168.20.19:3001", "verify_tls": false}
"""

import json
import os
import subprocess
import sys
import threading
import time
import webbrowser

import requests
from PIL import Image, ImageDraw, ImageFont
import pystray

# ---------- 配置 ----------

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE, "config.json")
DEFAULT_CONFIG = {"server": "https://192.168.20.19:3001", "verify_tls": False}


def load_config():
    if not os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, ensure_ascii=False, indent=2)
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = dict(DEFAULT_CONFIG)
        cfg.update(json.load(f))
    if not cfg.get("server"):
        print("请编辑 config.json 填入服务器地址, 例如 https://192.168.20.19:3001")
        sys.exit(1)
    return cfg


CFG = load_config()
SERVER = CFG["server"].rstrip("/")
EVENTS_URL = SERVER + "/notify/events"
VERIFY_TLS = CFG.get("verify_tls", False)
if not VERIFY_TLS:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ---------- 状态 ----------

state = {
    "connected": False,
    "paused": False,
    "unread": 0,
    "blink_on": False,
    "recent": [],   # 最近消息 dict 列表, 供悬浮 tooltip 展示
}
state_lock = threading.Lock()

# ---------- 托盘图标(纯 Pillow 绘制, 无需图标文件) ----------

def _font(size):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def make_icon():
    """根据状态绘制托盘图标: 未读时闪烁(橙高亮), 右上角未读角标"""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    with state_lock:
        connected, paused = state["connected"], state["paused"]
        unread, blink_on = state["unread"], state["blink_on"]
    blinking = unread > 0 and not paused
    if blinking and blink_on:
        base_color = (251, 146, 60, 255)       # 橙: 闪烁高亮相
    elif paused:
        base_color = (148, 163, 184, 255)      # 灰: 暂停
    elif connected:
        base_color = (34, 197, 94, 255)        # 绿: 在线
    else:
        base_color = (239, 68, 68, 255)        # 红: 断线
    d.rounded_rectangle([4, 8, 60, 60], radius=14, fill=base_color)
    f = _font(34)
    tb = d.textbbox((0, 0), "P", font=f)
    d.text(((64 - tb[2] + tb[0]) / 2 - tb[0], (68 - tb[3] + tb[1]) / 2 - tb[1]),
           "P", fill=(255, 255, 255, 255), font=f)
    if unread > 0 and not paused:
        n = str(unread) if unread <= 99 else "99+"
        bf = _font(15 if len(n) <= 2 else 12)
        bb = d.textbbox((0, 0), n, font=bf)
        w, h = bb[2] - bb[0], bb[3] - bb[1]
        cx, cy = 50, 12
        r = max(w, h) / 2 + 6
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(220, 38, 38, 255), outline=(255, 255, 255, 255), width=2)
        d.text((cx - w / 2 - bb[0], cy - h / 2 - bb[1]), n, fill=(255, 255, 255, 255), font=bf)
    return img


# pystray 的 Icon 没有 update() 方法 —— 直接给 icon.icon 赋值即触发重绘;
# 多线程(SSE/闪烁)并发赋值需串行化, 否则 win32 后端可能竞态
icon_lock = threading.Lock()


def _clip(s, n):
    """单行截断并压平换行(Windows tooltip 上限约 127 字符)"""
    s = (s or "").replace("\r", " ").replace("\n", " ").strip()
    return s if len(s) <= n else s[: max(n - 1, 1)] + "…"


def build_tooltip():
    """悬浮提示: 状态 + 未读数 + 最近几条消息摘要(QQ 式)"""
    with state_lock:
        connected, paused = state["connected"], state["paused"]
        unread = state["unread"]
        recent = list(state["recent"])
    status = "已暂停" if paused else ("已连接" if connected else "连接断开, 重试中…")
    head = "pim 消息提醒 (%s)" % status
    if not unread:
        return head
    lines = ["%s | 未读 %d 条:" % (head, unread)]
    for m in recent[-3:]:
        who = _clip(m.get("title") or "", 12)
        app = _clip(m.get("app") or "", 8)
        body = _clip(m.get("body") or "", 18)
        lines.append("%s %s: %s" % (app, who, body))
    return "\n".join(_clip(l, 40) for l in lines)[:126]


def refresh_icon(icon):
    try:
        with icon_lock:
            icon.icon = make_icon()
            icon.tooltip = build_tooltip()
    except Exception:
        pass


# ---------- 系统通知 ----------

def notify(title, body):
    try:
        if sys.platform == "win32":
            from winotify import Notification, audio
            n = Notification(app_id="pim", title=title or "新消息", msg=body or "")
            n.set_audio(audio.Silent, loop=False)
            n.show()
        elif sys.platform == "darwin":
            script = 'display notification "{}" with title "{}"'.format(
                (body or "").replace('"', '\\"'), (title or "新消息").replace('"', '\\"'))
            subprocess.Popen(["osascript", "-e", script])
        else:
            subprocess.Popen(["notify-send", title or "新消息", body or ""])
    except Exception as e:
        print("notify error:", e)


# ---------- 菜单动作 ----------

def open_desktop(icon=None, item=None):
    with state_lock:
        state["unread"] = 0
        state["blink_on"] = False
    refresh_icon(ICON)
    webbrowser.open(SERVER + "/")


def clear_unread(icon, item):
    with state_lock:
        state["unread"] = 0
        state["blink_on"] = False
    refresh_icon(icon)


def toggle_pause(icon, item):
    with state_lock:
        state["paused"] = not state["paused"]
    refresh_icon(icon)


def quit_app(icon, item):
    icon.stop()
    os._exit(0)


def build_menu():
    return pystray.Menu(
        pystray.MenuItem(lambda item: "状态: " + ("已暂停" if state["paused"] else
                      ("已连接 " + SERVER if state["connected"] else "连接断开, 重试中…")),
                      None, enabled=False),
        pystray.MenuItem(lambda item: "未读: %d" % state["unread"] if state["unread"] else "无未读",
                      None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("打开网页桌面", open_desktop, default=True),
        pystray.MenuItem(lambda item: "恢复提醒" if state["paused"] else "暂停提醒", toggle_pause),
        pystray.MenuItem("清除未读", clear_unread),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("退出", quit_app),
    )


# ---------- 消息处理 ----------

def handle(m, is_backlog):
    with state_lock:
        paused = state["paused"]
        rec = state["recent"]
        rec.append({"app": m.get("app") or "", "title": m.get("title") or "",
                    "body": m.get("body") or "", "ts": m.get("ts") or ""})
        del rec[:-10]
    if is_backlog:
        # 历史消息: 只累计未读, 不弹窗(与网页端一致)
        if not paused:
            with state_lock:
                state["unread"] += 1
    else:
        if not paused:
            notify(m.get("title") or m.get("app") or "新消息",
                   (("[%s] " % m["app"]) if m.get("app") else "") + (m.get("body") or ""))
            with state_lock:
                state["unread"] += 1
    refresh_icon(ICON)


# ---------- SSE 连接循环 ----------

def blink_loop():
    """有未读时让图标闪烁(0.5s 相位切换), 点开或清除未读后停止"""
    while True:
        time.sleep(0.5)
        with state_lock:
            active = state["unread"] > 0 and not state["paused"]
            if not active:
                if state["blink_on"]:
                    state["blink_on"] = False
                    need_refresh = True
                else:
                    need_refresh = False
            else:
                state["blink_on"] = not state["blink_on"]
                need_refresh = True
        if need_refresh:
            refresh_icon(ICON)


def sse_loop():
    backoff = 2
    while True:
        try:
            with requests.get(EVENTS_URL, stream=True, verify=VERIFY_TLS, timeout=(5, None)) as r:
                r.raise_for_status()
                backoff = 2
                with state_lock:
                    state["connected"] = True
                refresh_icon(ICON)
                event = "message"
                for line in r.iter_lines(decode_unicode=True):
                    if line is None:
                        continue
                    if line == "":
                        event = "message"
                        continue
                    if line.startswith("event:"):
                        event = line[6:].strip()
                        continue
                    if line.startswith("data:"):
                        try:
                            m = json.loads(line[5:].strip())
                        except ValueError:
                            continue
                        handle(m, is_backlog=(event == "backlog"))
        except Exception as e:
            with state_lock:
                state["connected"] = False
            refresh_icon(ICON)
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)


# ---------- 入口 ----------

ICON = pystray.Icon("pim", make_icon(), "pim 消息提醒", build_menu())

if __name__ == "__main__":
    # 原生级崩溃(pythonw 无控制台)落盘, 便于排查
    try:
        import faulthandler
        faulthandler.enable(open(os.path.join(BASE, "tray_crash.log"), "w"))
    except Exception:
        pass
    if "--test-once" in sys.argv:
        # 无界面测试: 连上后打印事件流前几条即退出
        with requests.get(EVENTS_URL, stream=True, verify=VERIFY_TLS, timeout=(5, 10)) as r:
            print("HTTP", r.status_code)
            n = 0
            event = "message"
            for line in r.iter_lines(decode_unicode=True):
                if line is None:
                    continue
                if line.startswith("event:"):
                    event = line[6:].strip()
                    print("EVENT", event)
                    continue
                if line.startswith("data:"):
                    print("DATA", line[5:].strip()[:120])
                    n += 1
                    if n >= 3:
                        break
        sys.exit(0)
    threading.Thread(target=sse_loop, daemon=True).start()
    threading.Thread(target=blink_loop, daemon=True).start()
    ICON.run()
