# pim — 浏览器远程 IM 工作站

把 QQ / 微信 / 钉钉 三个官方 Linux 客户端装进一个 webtop 容器，浏览器远程操作。

**核心特性：桌面消息提醒** —— 任何客户端收到新消息，浏览器立即弹出系统级
通知（右下角 Toast），人不在客户端页面也能第一时间看到谁发了什么；同时提供
实时消息列表页，历史消息随时回看。也可选转发到手机（ntfy / Bark）。

多架构镜像（**linux/amd64 + linux/arm64**）由 GitHub Actions 自动构建并发布
到 ghcr.io。

```
QQ/微信/钉钉 ──桌面通知──> DBus ──dbus-monitor──> notify_relay.py
                                        ├─ SSE ──> 浏览器推送页(列表+系统弹窗)
                                        └─ 可选 ─> ntfy/Bark ──> 手机 App
```

> **免责声明**
>
> 1. 本项目仅供个人学习与效率工具用途，与腾讯（QQ/微信）、阿里巴巴（钉钉）
>    无任何关联，亦非官方产品。
> 2. 项目通过旁听 Linux 桌面系统通知（DBus）获取消息摘要，不修改、不注入、
>    不逆向客户端，不触碰任何私有协议；但仍请遵守各平台用户协议与服务条款，
>    由此产生的任何账号风险、纠纷及损失由使用者自行承担。
> 3. 消息提醒仅基于客户端自身弹出的通知内容，可能存在延迟、截断或遗漏，
>    请勿将其作为重要事务的唯一通知渠道。
> 4. 请勿将本软件用于任何违法违规用途；使用本项目即表示你已知悉并接受上述条款。

## 部署步骤

### 1. 准备目录与配置

```bash
mkdir -p pim/{data,downloads,documents,pictures,certs}
cd pim
curl -O https://raw.githubusercontent.com/wune5/pim/main/docker-compose.yml
curl -O https://raw.githubusercontent.com/wune5/pim/main/Caddyfile
curl -O https://raw.githubusercontent.com/wune5/pim/main/scripts/gen-cert.sh
chmod +x gen-cert.sh
./gen-cert.sh        # 生成自签名证书(自动包含本机 IP, 换网络环境可指定: ./gen-cert.sh 192.168.x.x)
```

目录映射关系（compose 已配好）：

| 宿主机目录 | 容器内路径 | 用途 |
|---|---|---|
| `./data` | `/config` | 登录态、聊天记录、整个桌面 HOME |
| `./downloads` | `/config/Downloads` | 桌面「下载」文件夹，与宿主机互通 |
| `./documents` | `/config/Documents` | 桌面「文档」文件夹 |
| `./pictures` | `/config/Pictures` | 桌面「图片」文件夹 |
| `./certs` | `/certs` | 统一入口的自签名证书 |

> 文件夹映射是双向的：往宿主机 `./downloads` 放文件，容器桌面里立刻可见；
> 微信里收到的文件存到 Downloads，宿主机直接就能取。注意宿主机目录属主
> 需与 `PUID/PGID`（默认 1000）一致，否则容器内可能只读。

### 2. 启动

```bash
docker compose up -d
docker compose logs -f        # 观察启动，无报错后 Ctrl+C
```

需要手机推送时，在 compose 的 `environment` 里加一行
`NTFY_URL=https://ntfy.sh/你的私密topic`（topic 名要随机，相当于密码）后
`docker compose up -d` 重启。

### 3. 登录客户端

1. 浏览器打开 **`https://IP:3001`**（首次需接受自签名证书警告）
2. 在桌面里依次打开 QQ / 微信 / 钉钉，手机扫码登录
3. 三个客户端的设置里**开启「消息通知」**（关勿扰、群别静音），
   否则收不到提醒
4. 登录态保存在 `./data`，容器重启无需重新扫码

### 4. 开启桌面消息提醒（核心）

- 浏览器打开 **`https://IP:3001/relay/`**（和桌面共用同一个端口）
- 点右上角「**开启浏览器通知**」并在浏览器弹窗中选择「允许」
- 之后任何客户端收到新消息，都会：
  - 在页面出现消息卡片（实时，可回看最近 200 条）
  - **弹出系统级通知**（Windows 右下角 Toast 式弹窗，即使浏览器在后台）
- 手机：ntfy App 订阅同一 topic（见上），页面不开也能收
- 想回复消息：切回桌面直接操作客户端

> **端口统一说明**：3001 由内置的 Caddy 反代统一入口——`/` 是桌面，
> `/relay/` 是消息提醒页面；webtop 自身的 3001/8443 和中继的 8443
> 都只在容器网络内部，不再直接对外暴露。另有 `http://IP:8787` HTTP
> 直连入口可用（只有消息列表，浏览器弹窗需 HTTPS）。

### 5. 验证提醒链路

容器桌面终端里执行：

```bash
notify-send "微信" "张三: 测试消息"
```

浏览器页面立即出现这条并弹系统通知即链路正常。收不到时先检查客户端的
通知开关，再看容器日志 `docker compose logs im-desktop`。

### 6. 常用维护命令

```bash
docker compose pull && docker compose up -d   # 更新镜像（每周一自动出新版）
docker compose restart im-desktop             # 重启
tar czf data-backup.tgz data                  # 备份登录态
```

## 客户端能力

| 能力 | 支持情况 |
|---|---|
| **桌面消息提醒（浏览器弹窗）** | ✅ 核心（https://IP:3001/relay/） |
| **消息列表实时刷新** | ✅ SSE + 断线自动重连 |
| 浏览器复制文本进容器 | ✅ 双向 |
| 浏览器贴图（如截图进微信） | ✅ 双向 |
| 浏览器拖拽/下载文件 | ✅（侧边栏文件管理器） |
| 回复消息 | 在浏览器桌面里直接操作客户端 |

## 镜像构建

`docker build` 时通过 build-args 传入三个客户端 deb 的下载地址
（按 `TARGETARCH` 自动选 amd64/arm64 包）：

```bash
docker build -t pim . \
  --build-arg WECHAT_URL_AMD64=... --build-arg WECHAT_URL_ARM64=... \
  --build-arg QQ_URL_AMD64=... --build-arg QQ_URL_ARM64=... \
  --build-arg DINGTALK_URL_AMD64=... --build-arg DINGTALK_URL_ARM64=...
```

不传时使用 Dockerfile 里的默认版本。`scripts/resolve-urls.sh` 可独立运行，
打印当前解析出的最新地址（本地跑会直接输出结果）。

### GitHub Actions 自动构建

`.github/workflows/build.yml`：
1. `resolve-urls` job 运行解析脚本，自动跟踪三个客户端的最新版本；
2. `build` job 在 **amd64（ubuntu-latest）和 arm64（ubuntu-24.04-arm）原生 runner**
   上并行构建并按 digest 推送（arm 原生 runner 对公开仓库免费，比 QEMU 快得多）；
3. `merge` job 把两个 digest 合并成多架构 manifest，打 `latest` 和 `sha-xxxxxxx` 标签。

产物：`ghcr.io/<owner>/<repo>:latest`（多架构 manifest，docker pull 自动匹配架构）。

注意：私有仓库没有免费 arm runner，把 matrix 里的 `ubuntu-24.04-arm` 换回
`ubuntu-latest` 并让 buildx 走 QEMU 即可（构建时间会显著变长）。

## 已知限制

- 消息提醒是**只读**的：仅收到通知里的文本摘要，回复需在浏览器桌面里操作客户端。
- 长消息会被客户端通知截断；图片/语音/文件只显示提示文字。
- 窗口聚焦时部分应用可能不发通知。
- 浏览器系统弹窗依赖 HTTPS 安全上下文（本项目默认即 HTTPS，HTTP 直连入口只有列表）。

## 三个客户端的官方下载源

| 客户端 | 地址 | arm64 |
|---|---|---|
| QQ | https://im.qq.com/linuxqq/index.html | ✅ |
| 微信 | https://linux.weixin.qq.com/ | ✅（选「ARM 版」） |
| 钉钉 | https://www.dingtalk.com/ 下载页 | ✅ |
