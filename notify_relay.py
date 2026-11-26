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
import glob
import json
import os
import queue
import re
import ssl
import subprocess
import threading
import time
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("PORT", "8787"))
TLS_PORT = int(os.environ.get("TLS_PORT", "8443"))
NTFY_URL = os.environ.get("NTFY_URL", "").strip()   # 例: https://ntfy.sh/my-secret-topic
MAX_KEEP = 200                                       # 页面保留的历史条数

STRING_RE = re.compile(r'^\s*string "(.*)"\s*$')


def ensure_tls_cert(certdir: str):
    """生成自签名证书(存在则跳过)。浏览器弹窗 API 只在 HTTPS/localhost 下可用,
    所以必须有一个 HTTPS 入口。"""
    cert = os.path.join(certdir, "cert.pem")
    key = os.path.join(certdir, "key.pem")
    if os.path.exists(cert) and os.path.exists(key):
        return cert, key
    os.makedirs(certdir, exist_ok=True)
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
         "-keyout", key, "-out", cert, "-days", "3650",
         "-subj", "/CN=pim-notify-relay"],
        capture_output=True, timeout=60, check=True)
    return cert, key


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
    # Notify 参数顺序: app_name, app_icon, summary, body (均为 string,
    # 中间的 replaces_id 是 uint32, 在收集时被跳过)
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


def find_session_bus():
    """自动发现桌面会话总线: 遍历 /tmp/dbus-* socket,
    挑"通知守护进程所在 + 服务名最多"的那个 —— 桌面会话总线服务名
    几十个, 无意中 autolaunch 出来的野总线只有几个, 以此区分。
    webtop 容器重启后 socket 名会变, autostart 环境也不一定带全, 靠这个自愈。"""
    best, best_names = None, -1
    for path in sorted(glob.glob("/tmp/dbus-*")):
        addr = "unix:path=" + path
        env = dict(os.environ, DBUS_SESSION_BUS_ADDRESS=addr)
        # 注意: 不能用 dbus-send --address= 参数(该版本有未注册就发消息的
        # bug), 必须用 DBUS_SESSION_BUS_ADDRESS 环境变量传入
        try:
            r = subprocess.run(
                ["dbus-send", "--print-reply",
                 "--dest=org.freedesktop.DBus", "/",
                 "org.freedesktop.DBus.GetNameOwner",
                 "string:org.freedesktop.Notifications"],
                capture_output=True, timeout=5, env=env)
            if r.returncode != 0:
                continue  # 没有通知守护进程, 不是桌面会话总线
            n = subprocess.run(
                ["dbus-send", "--print-reply",
                 "--dest=org.freedesktop.DBus", "/",
                 "org.freedesktop.DBus.ListNames"],
                capture_output=True, timeout=5, env=env)
            count = n.stdout.count(b'string "')
            print(f"[bus] {path}: 通知服务在, 服务名 {count} 个", flush=True)
            if count > best_names:
                best, best_names = addr, count
        except Exception as e:
            print(f"[bus] {path}: 探测异常 {e}", flush=True)
    if best:
        return best
    return None


def dbus_loop():
    """监听 session bus 上的 org.freedesktop.Notifications.Notify 调用"""
    # 不用 match 规则: dbus-monitor 新版解析器对带引号规则语法兼容性差,
    # 裸监听全量事件后由下方解析器按 member=Notify 过滤, 实测最稳
    cmd = ["dbus-monitor", "--session"]
    while True:
        try:
            addr = find_session_bus() or os.environ.get("DBUS_SESSION_BUS_ADDRESS")
            if not addr:
                print("未找到会话总线, 5 秒后重试 ...", flush=True)
                time.sleep(5)
                continue
            print("监听总线:", addr, flush=True)
            env = dict(os.environ, DBUS_SESSION_BUS_ADDRESS=addr)
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL, text=True,
                                    errors="replace", env=env)
            collecting = False
            strings = []
            for line in proc.stdout:
                s = line.rstrip("\n")
                if "member=Notify" in s and s.startswith("method call "):
                    collecting = True
                    strings = []
                    continue
                if not collecting:
                    continue
                m = STRING_RE.match(s)
                if m:
                    strings.append(unescape(m.group(1)))
                    if len(strings) == 4:
                        # Notify 前 4 个 string 依次是:
                        # app_name, app_icon, summary, body
                        collecting = False
                        handle_notify(strings)
                        strings = []
        except Exception as e:
            print("dbus-monitor 异常:", e)
        print("3 秒后重连 dbus-monitor ...", flush=True)
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
document.getElementById('enable').onclick=()=>{
  if(!window.isSecureContext){alert('当前是 HTTP 访问, 浏览器不允许弹系统通知。\n请改用 https://'+location.hostname+':8443 访问(首次打开点"高级→继续前往")。');return}
  Notification.requestPermission();
};
if(!window.isSecureContext){
  const tip=document.createElement('div');
  tip.style.cssText='background:#fef3c7;color:#92400e;border:1px solid #fbbf24;border-radius:8px;padding:8px 12px;margin:0 12px;font-size:13px';
  tip.textContent='提示: HTTP 页面只能看消息列表, 不能弹系统通知。要弹窗请用 https://'+location.hostname+':8443 访问。';
  const main=document.querySelector('main');
  main.parentNode.insertBefore(tip,main);
}
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
    print(f"IM 通知中继已启动: http://0.0.0.0:{PORT}", flush=True)
    if NTFY_URL:
        print(f"ntfy 转发已开启: {NTFY_URL}", flush=True)
    # HTTP 入口(消息列表)放后台线程
    threading.Thread(
        target=ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever,
        daemon=True).start()
    # HTTPS 入口(浏览器弹窗 API 要求安全上下文)
    try:
        cert, key = ensure_tls_cert("/config/.config/notify-relay")
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        httpsd = ThreadingHTTPServer(("0.0.0.0", TLS_PORT), Handler)
        httpsd.socket = ctx.wrap_socket(httpsd.socket, server_side=True)
        print(f"HTTPS 已开启: https://0.0.0.0:{TLS_PORT} (浏览器弹窗走这个)", flush=True)
        httpsd.serve_forever()
    except Exception as e:
        print(f"HTTPS 启动失败(弹窗功能不可用, 消息列表不受影响): {e}", flush=True)
        while True:
            time.sleep(3600)
