#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
IM 通知中继：监听 Linux 桌面通知(DBus) -> 浏览器页面实时推送(SSE) -> 可选转发 ntfy

用法:
    python3 notify_relay.py                 # 默认端口 8787
    PORT=9000 python3 notify_relay.py       # 指定端口

浏览器打开 http://<机器IP>:8787 即可实时看到 QQ/微信的消息通知。

测试(在装有桌面环境的机器上):
    notify-send "微信" "张三: 晚上吃饭吗"

转发到手机/关页也能收:
    NTFY_URL=https://ntfy.sh/你的私密topic python3 notify_relay.py
    手机装 ntfy App 订阅同一 topic 即可。

要求:
    - 必须在桌面会话内运行(能访问 session bus, webtop 容器里直接跑即可)
    - QQ/微信客户端需开启"消息通知"选项
    - 无第三方 Python 依赖, 仅用标准库
"""
import json
import os
import re
import queue
import subprocess
import threading
import time
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("PORT", "8787"))
NTFY_URL = os.environ.get("NTFY_URL", "").strip()   # 例: https://ntfy.sh/my-secret-topic
MAX_KEEP = 200                                       # 页面保留的历史条数

STRING_RE = re.compile(r'^\s*string "(.*)"\s*$')


def unescape(s: str) -> str:
    """dbus-monitor 会转义字符串内的引号/反斜杠"""
    return s.replace('\\"', '"').replace("\\\\", "\\")


class Relay:
    def __init__(self):
        self.lock = threading.Lock()
        self.messages = []   # [{app,title,body,ts}]
        self.clients = []    # 每个浏览器连接一个 Queue

    def push(self, msg):
        with self.lock:
            self.messages.append(msg)
            del self.messages[:-MAX_KEEP]
            dead = []
            for q in self.clients:
                try:
                    q.put_nowait(msg)
                except Exception:
                    dead.append(q)
            for q in dead:
                self.clients.remove(q)

    def backlog(self, n=100):
        with self.lock:
            return list(self.messages[-n:])


relay = Relay()
_last = {"key": "", "ts": 0.0}


def handle_notify(strings):
    # Notify 参数顺序: app_name, replaces_id, app_icon, summary, body, ...
    if len(strings) < 4:
        return
    app, summary, body = strings[0], strings[2], strings[3]
    if not summary and not body:
        return
    key = (app, summary, body)
    now = time.time()
    # 简单去重: 60 秒内完全相同的通知只推一次
    if key == (_last["key"]) and now - _last["ts"] < 60:
        return
    _last["key"] = key
    _last["ts"] = now

    msg = {
        "app": app or "通知",
        "title": summary,
        "body": body,
        "ts": datetime.now().strftime("%m-%d %H:%M:%S"),
    }
    relay.push(msg)
    print(f"[{msg['ts']}] [{msg['app']}] {msg['title']}: {msg['body']}")

    if NTFY_URL:
        try:
            text = f"[{msg['app']}] {msg['title']}\n{msg['body']}".encode("utf-8")
            req = urllib.request.Request(NTFY_URL, data=text,
                                         headers={"Content-Type": "text/plain",
                                                  "Title": msg["app"],
                                                  "X-Tags": "email"})
            urllib.request.urlopen(req, timeout=5)
        except Exception as e:
            print("ntfy 转发失败:", e)


def dbus_loop():
    """监听 session bus 上的 org.freedesktop.Notifications.Notify 调用"""
    cmd = ["dbus-monitor", "--session",
           "interface='org.freedesktop.Notifications',member='Notify'"]
    while True:
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL, text=True,
                                    errors="replace")
            collecting = False
            strings = []
            for line in proc.stdout:
                s = line.rstrip("\n")
                if "member=Notify" in s:
                    collecting = True
                    strings = []
                    continue
                if collecting:
                    m = STRING_RE.match(s)
                    if m:
                        strings.append(unescape(m.group(1)))
                        continue
                    # 遇到第一个非 string 参数(如 uint32/array)即收集结束
                    collecting = False
                    handle_notify(strings)
        except Exception as e:
            print("dbus-monitor 异常:", e)
        print("3 秒后重连 dbus-monitor ...")
        time.sleep(3)


PAGE = """<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>IM 消息推送</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
 body{font-family:system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;margin:0;background:#f5f6f8;color:#222}
 header{position:sticky;top:0;z-index:1;display:flex;align-items:center;gap:10px;padding:10px 16px;background:#fff;border-bottom:1px solid #e3e5e8}
 #dot{width:10px;height:10px;border-radius:50%;background:#ccc;flex:none}
 #dot.on{background:#22c55e}#dot.off{background:#ef4444}
 h1{font-size:16px;margin:0;flex:1}
 button{border:1px solid #d0d3d8;background:#fff;border-radius:6px;padding:4px 12px;cursor:pointer;font-size:13px}
 main{max-width:640px;margin:0 auto;padding:12px}
 .msg{background:#fff;border:1px solid #e3e5e8;border-radius:10px;padding:10px 14px;margin-bottom:10px;animation:fade .2s}
 .meta{font-size:12px;color:#888;display:flex;justify-content:space-between;gap:8px;margin-bottom:4px}
 .app{background:#eef1ff;color:#4152d8;border-radius:4px;padding:1px 8px;margin-right:8px}
 .title{font-weight:600}
 .body{white-space:pre-wrap;word-break:break-all}
 @keyframes fade{from{opacity:0;transform:translateY(-4px)}to{opacity:1}}
 .empty{color:#999;text-align:center;padding:40px 0}
</style></head><body>
<header><span id="dot" class="off"></span><h1>IM 消息推送</h1>
<button id="enable">开启浏览器通知</button><button id="clear">清空</button></header>
<main id="list"><div class="empty">等待消息…（可用 notify-send "微信" "测试" 验证）</div></main>
<script>
const list=document.getElementById('list'),dot=document.getElementById('dot');
const empty=list.innerHTML;
function esc(s){const d=document.createElement('div');d.textContent=s==null?'':String(s);return d.innerHTML}
function add(m){
  if(list.querySelector('.empty'))list.innerHTML='';
  const el=document.createElement('div');el.className='msg';
  el.innerHTML='<div class="meta"><span><span class="app">'+esc(m.app)+'</span><span class="title">'+esc(m.title)+'</span></span><span>'+esc(m.ts)+'</span></div><div class="body">'+esc(m.body)+'</div>';
  list.prepend(el);
  if('Notification'in window&&Notification.permission==='granted'){
    try{new Notification(m.title,{body:m.body})}catch(e){}
  }
}
document.getElementById('enable').onclick=()=>Notification.requestPermission();
document.getElementById('clear').onclick=()=>list.innerHTML=empty;
function connect(){
  const es=new EventSource('/events');
  es.onopen=()=>dot.className='on';
  es.onerror=()=>{dot.className='off';es.close();setTimeout(connect,3000)};
  es.onmessage=e=>add(JSON.parse(e.data));
}
connect();
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/events"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            q = queue.Queue()
            with relay.lock:
                relay.clients.append(q)
            try:
                for m in relay.backlog():
                    self.wfile.write(f"data: {json.dumps(m, ensure_ascii=False)}\n\n".encode("utf-8"))
                self.wfile.flush()
                while True:
                    m = q.get()
                    self.wfile.write(f"data: {json.dumps(m, ensure_ascii=False)}\n\n".encode("utf-8"))
                    self.wfile.flush()
            except (ConnectionError, BrokenPipeError):
                pass
            finally:
                with relay.lock:
                    if q in relay.clients:
                        relay.clients.remove(q)
        else:
            body = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    threading.Thread(target=dbus_loop, daemon=True).start()
    print(f"IM 通知中继已启动: http://0.0.0.0:{PORT}")
    if NTFY_URL:
        print(f"ntfy 转发已开启: {NTFY_URL}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
