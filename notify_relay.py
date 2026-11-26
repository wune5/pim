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
<title>pim - IM 桌面</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
 html,body{margin:0;height:100%;overflow:hidden;font-family:system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;background:#111}
 #desk{position:fixed;inset:0;width:100%;height:100%;border:0;display:block}
 /* 左下角连接状态点: 绿=SSE在线 红=断开, 点击查看最近消息 */
 #dot{position:fixed;left:10px;bottom:10px;z-index:50;width:12px;height:12px;border-radius:50%;background:#666;border:2px solid rgba(0,0,0,.35);cursor:pointer;box-shadow:0 1px 4px rgba(0,0,0,.4)}
 #dot.on{background:#22c55e}#dot.off{background:#ef4444}
 /* 通知权限开启横幅 */
 #ask{position:fixed;top:12px;left:50%;transform:translateX(-50%);z-index:60;display:none;align-items:center;gap:10px;background:#fff;border:1px solid #e3e5e8;border-radius:10px;padding:10px 16px;box-shadow:0 4px 16px rgba(0,0,0,.15);font-size:14px;color:#333}
 #ask button{border:0;background:#2563eb;color:#fff;border-radius:6px;padding:6px 14px;cursor:pointer;font-size:13px}
 #ask .x{cursor:pointer;color:#999;font-size:16px;padding:0 2px}
 /* 右下角通知弹层 */
 #toasts{position:fixed;right:14px;bottom:14px;z-index:55;display:flex;flex-direction:column;gap:8px;max-width:360px}
 .toast{background:#fff;border:1px solid #e3e5e8;border-radius:10px;padding:10px 14px;box-shadow:0 4px 16px rgba(0,0,0,.18);animation:slide .18s;cursor:pointer}
 .toast .meta{font-size:12px;color:#888;display:flex;justify-content:space-between;gap:8px;margin-bottom:3px}
 .toast .app{background:#eef1ff;color:#4152d8;border-radius:4px;padding:1px 8px;margin-right:8px}
 .toast .title{font-weight:600;color:#222}
 .toast .body{font-size:13px;color:#444;white-space:pre-wrap;word-break:break-all;max-height:120px;overflow:hidden}
 @keyframes slide{from{opacity:0;transform:translateY(8px)}to{opacity:1}}
 /* 最近消息面板 */
 #panel{position:fixed;left:0;bottom:0;z-index:60;display:none;width:340px;max-height:60vh;overflow-y:auto;background:#fff;border-top:1px solid #e3e5e8;border-right:1px solid #e3e5e8;border-radius:0 12px 0 0;box-shadow:0 4px 16px rgba(0,0,0,.2)}
 #panel h2{font-size:14px;margin:0;padding:10px 14px;border-bottom:1px solid #eee;display:flex;justify-content:space-between;align-items:center;color:#333}
 #panel h2 span.x{cursor:pointer;color:#999;font-size:16px}
 #panel .msg{padding:8px 14px;border-bottom:1px solid #f2f3f5;font-size:13px}
 #panel .meta{font-size:12px;color:#888;margin-bottom:2px}
 #panel .app{background:#eef1ff;color:#4152d8;border-radius:4px;padding:1px 6px;margin-right:8px}
 #panel .title{font-weight:600;color:#222}
 #panel .body{color:#444;white-space:pre-wrap;word-break:break-all}
</style></head><body>
<iframe id="desk" title="桌面" src="about:blank" allow="clipboard-read; clipboard-write; fullscreen"></iframe>
<span id="dot" class="off" title="通知连接状态, 点击查看最近消息"></span>
<div id="ask"><span>开启桌面通知, 收到新消息时系统弹窗提醒</span><button id="askbtn">开启</button><span class="x" id="askx">✕</span></div>
<div id="toasts"></div>
<div id="panel"><h2>最近消息<span class="x" id="px">✕</span></h2><div id="plist"></div></div>
<script>
const dot=document.getElementById('dot'),desk=document.getElementById('desk');
const toasts=document.getElementById('toasts'),panel=document.getElementById('panel'),plist=document.getElementById('plist');
// 经 caddy 的 /relay/ 打开时嵌同源桌面; 直连 relay(8443/8787 根路径)时嵌统一入口桌面
desk.src = /\\/relay\\/?$/.test(location.pathname) ? '/' : location.protocol+'//'+location.hostname+':3001/';
function esc(s){const d=document.createElement('div');d.textContent=s==null?'':String(s);return d.innerHTML}
function show(m,alert){  const meta='<div class="meta"><span><span class="app">'+esc(m.app)+'</span><span class="title">'+esc(m.title)+'</span></span><span>'+esc(m.ts)+'</span></div>';
  if(alert){
    const el=document.createElement('div');el.className='toast';
    el.innerHTML=meta+'<div class="body">'+esc(m.body)+'</div>';
    el.onclick=()=>el.remove();
    toasts.appendChild(el);
    setTimeout(()=>el.remove(),8000);
    if('Notification'in window&&Notification.permission==='granted'){
      try{new Notification(m.title||m.app,{body:(m.app?'['+m.app+'] ':'')+(m.body||'')})}catch(e){}
    }
  }else{
    const el=document.createElement('div');el.className='msg';
    el.innerHTML=meta+'<div class="body">'+esc(m.body)+'</div>';
    plist.prepend(el);
  }
}
dot.onclick=()=>panel.style.display=panel.style.display==='block'?'none':'block';
document.getElementById('px').onclick=()=>panel.style.display='none';
const ask=document.getElementById('ask');
function refreshAsk(){ask.style.display=('Notification'in window&&Notification.permission==='default')?'flex':'none'}
document.getElementById('askbtn').onclick=async()=>{try{await Notification.requestPermission()}catch(e){}refreshAsk()};
document.getElementById('askx').onclick=()=>ask.style.display='none';
refreshAsk();
function connect(){
  // 相对路径: 直连时解析为 /events, 经反代挂在 /relay/ 下时解析为 /relay/events
  const es=new EventSource('events');
  es.onopen=()=>dot.className='on';
  es.onerror=()=>{dot.className='off';es.close();setTimeout(connect,3000)};
  // 历史消息(backlog)只入列表; 实时消息才 toast + 系统通知
  es.addEventListener('backlog',e=>show(JSON.parse(e.data),false));
  es.onmessage=e=>show(JSON.parse(e.data),true);
}
connect();
</script></body></html>"""


# 注入到 Selkies 桌面页面的通知脚本(由 Dockerfile sed 写入 index.html)。
# 桌面原生打开零开销; 通知面板作为侧栏 section 注入(跟随 Selkies 主题),
# 右下角 toast + 系统弹窗 + 侧栏消息列表三路呈现。
INJECT_JS = """(function(){
if(window.__PIM_NOTIFY__)return;window.__PIM_NOTIFY__=1;
const css=document.createElement('style');
css.textContent=`#pim-ask{position:fixed;top:12px;left:50%;transform:translateX(-50%);z-index:2147483001;display:none;align-items:center;gap:10px;background:#fff;border:1px solid #e3e5e8;border-radius:10px;padding:10px 16px;box-shadow:0 4px 16px rgba(0,0,0,.18);font-size:14px;color:#333}
#pim-ask button{border:0;background:#2563eb;color:#fff;border-radius:6px;padding:6px 14px;cursor:pointer;font-size:13px}
#pim-ask .x{cursor:pointer;color:#999;font-size:16px;padding:0 2px}
#pim-toasts{position:fixed;right:14px;bottom:14px;z-index:2147483000;display:flex;flex-direction:column;gap:8px;max-width:360px}
.pim-toast{background:#fff;border:1px solid #e3e5e8;border-radius:10px;padding:10px 14px;box-shadow:0 4px 16px rgba(0,0,0,.18);animation:pim-slide .18s;cursor:pointer}
.pim-toast .meta{font-size:12px;color:#888;display:flex;justify-content:space-between;gap:8px;margin-bottom:3px}
.pim-toast .app{background:#eef1ff;color:#4152d8;border-radius:4px;padding:1px 8px;margin-right:8px}
.pim-toast .title{font-weight:600;color:#222}
.pim-toast .body{font-size:13px;color:#444;white-space:pre-wrap;word-break:break-all;max-height:120px;overflow:hidden}
@keyframes pim-slide{from{opacity:0;transform:translateY(8px)}to{opacity:1}}
#pim-sec-content{max-height:320px;overflow-y:auto;padding:10px 12px;display:flex;flex-direction:column;gap:8px;background:var(--sk-surface)}
.pim-msg{background:var(--sk-surface-alt);border:1px solid var(--sk-border-soft);border-radius:8px;padding:8px 10px;font-size:13px;color:var(--sk-text)}
.pim-msg .meta{font-size:11px;color:var(--sk-text-muted);display:flex;justify-content:space-between;gap:8px;margin-bottom:2px}
.pim-msg .app{background:var(--sk-accent-soft);color:var(--sk-accent-text);border-radius:4px;padding:1px 6px;margin-right:8px}
.pim-msg .title{font-weight:600}
.pim-msg .body{white-space:pre-wrap;word-break:break-all;max-height:80px;overflow:hidden}
#pim-sec-badge{background:#ef4444;color:#fff;font-size:11px;line-height:1;padding:3px 7px;border-radius:8px;font-weight:700;display:none}
#pim-sec-badge[data-n="0"]{display:none}
.pim-empty{color:var(--sk-text-muted);font-size:12px;text-align:center;padding:6px 0}`;
document.head.appendChild(css);
const toasts=document.createElement('div');toasts.id='pim-toasts';document.body.appendChild(toasts);
const ask=document.createElement('div');ask.id='pim-ask';
ask.innerHTML='<span>开启桌面通知, 收到新消息时系统弹窗提醒</span><button>开启</button><span class="x">✕</span>';
document.body.appendChild(ask);
const askBtn=ask.querySelector('button');
function refreshAsk(){ask.style.display=('Notification'in window&&Notification.permission==='default')?'flex':'none'}
askBtn.onclick=async()=>{try{await Notification.requestPermission()}catch(e){}refreshAsk()};
ask.querySelector('.x').onclick=()=>ask.style.display='none';
refreshAsk();
function esc(s){const d=document.createElement('div');d.textContent=s==null?'':String(s);return d.innerHTML}
// ---- 侧栏面板: 等待 Selkies .sidebar 出现后注入一个 section ----
let secList=null,secBadge=null,unread=0;
function buildMsg(m){
  const el=document.createElement('div');el.className='pim-msg';
  el.innerHTML='<div class="meta"><span><span class="app">'+esc(m.app)+'</span><span class="title">'+esc(m.title)+'</span></span><span>'+esc(m.ts)+'</span></div><div class="body">'+esc(m.body)+'</div>';
  return el;
}
function injectSidebar(){
  const bar=document.querySelector('.sidebar');
  if(!bar||bar.querySelector('#pim-sec'))return;
  const sec=document.createElement('div');sec.className='sidebar-section';sec.id='pim-sec';
  sec.innerHTML='<div class="sidebar-section-header" id="pim-sec-head" aria-expanded="true"><h3>IM 消息提醒</h3><span id="pim-sec-badge" data-n="0">0</span></div><div class="sidebar-section-content" id="pim-sec-content"><div class="pim-empty">暂无消息, 等待推送…</div></div>';
  bar.appendChild(sec);
  secList=sec.querySelector('#pim-sec-content');
  secBadge=sec.querySelector('#pim-sec-badge');
  sec.querySelector('#pim-sec-head').onclick=()=>{
    const open=sec.querySelector('#pim-sec-head').getAttribute('aria-expanded')==='true';
    sec.querySelector('#pim-sec-head').setAttribute('aria-expanded',open?'false':'true');
    secList.style.display=open?'none':'flex';
    if(!open){unread=0;secBadge.dataset.n='0'}
  };
  // 侧栏列表最多显示 50 条, 与 add() 的裁剪逻辑一致
  backlog.slice(-50).forEach(m=>{const first=secList.querySelector('.pim-empty');if(first)first.remove();secList.appendChild(buildMsg(m))});
}
// React 可能重渲染侧栏, 被移除时重新插入
const mo=new MutationObserver(()=>injectSidebar());
function startWatch(){mo.observe(document.body,{childList:true,subtree:true});injectSidebar()}
// ---- toast / 系统通知 / 未读计数 ----
// quiet=true 为历史消息(backlog): 只入侧栏列表, 不弹提醒不计未读
const backlog=[];
function add(m,quiet){
  backlog.push(m);if(backlog.length>100)backlog.shift();
  if(secList){const e=secList.querySelector('.pim-empty');if(e)e.remove();secList.prepend(buildMsg(m));
    while(secList.children.length>50)secList.lastChild.remove();}
  if(quiet)return;
  unread++;
  if(secBadge){secBadge.dataset.n=unread;secBadge.textContent=unread}
  const el=document.createElement('div');el.className='pim-toast';
  el.innerHTML='<div class="meta"><span><span class="app">'+esc(m.app)+'</span><span class="title">'+esc(m.title)+'</span></span><span>'+esc(m.ts)+'</span></div><div class="body">'+esc(m.body)+'</div>';
  el.onclick=()=>el.remove();
  toasts.appendChild(el);
  setTimeout(()=>el.remove(),8000);
  if('Notification'in window&&Notification.permission==='granted'){
    try{new Notification(m.title||m.app,{body:(m.app?'['+m.app+'] ':'')+(m.body||'')})}catch(e){}
  }
}
function connect(){
  const es=new EventSource('/notify/events');
  es.addEventListener('backlog',e=>add(JSON.parse(e.data),true));
  es.onerror=()=>{es.close();setTimeout(connect,3000)};
  es.onmessage=e=>add(JSON.parse(e.data));
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',startWatch);else startWatch();
connect();
})();"""


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
                # 历史消息用命名事件 backlog, 客户端只入列表不弹提醒;
                # 实时消息走默认 message 事件
                for m in relay.backlog():
                    self.wfile.write(f"event: backlog\ndata: {json.dumps(m, ensure_ascii=False)}\n\n".encode("utf-8"))
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
        elif self.path.startswith("/inject.js"):
            body = INJECT_JS.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            body = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def log_message(self, fmt, *args):
        # 访问日志落到 /tmp/relay.log, 便于排查浏览器端连接问题
        try:
            with open("/tmp/relay.log", "a", encoding="utf-8") as f:
                f.write(f"{datetime.now().strftime('%H:%M:%S')} {self.client_address[0]} "
                        f"{self.command} {self.path} -> {args[1] if len(args) > 1 else ''}\n")
        except Exception:
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
    # 失败自动重试: 容器启动早期可能有短暂竞态(挂载/端口未就绪), 不能一次失败就永久放弃
    while True:
        try:
            cert, key = ensure_tls_cert("/config/.config/notify-relay")
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(cert, key)
            httpsd = ThreadingHTTPServer(("0.0.0.0", TLS_PORT), Handler)
            httpsd.socket = ctx.wrap_socket(httpsd.socket, server_side=True)
            print(f"HTTPS 已开启: https://0.0.0.0:{TLS_PORT} (浏览器弹窗走这个)", flush=True)
            httpsd.serve_forever()
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"HTTPS 启动失败({e}), 10 秒后重试 ...", flush=True)
            time.sleep(10)
