# im-desktop

把 QQ / 微信 / 钉钉 三个官方 Linux 客户端装进一个 webtop 容器，浏览器远程操作，
并用桌面通知中继把新消息实时推送到浏览器页面（或手机 ntfy）。多架构镜像
（**linux/amd64 + linux/arm64**）由 GitHub Actions 自动构建并发布到 ghcr.io。

```
QQ/微信/钉钉 ──桌面通知──> DBus ──dbus-monitor──> notify_relay.py
                                        ├─ SSE ──> 浏览器 http://IP:8787
                                        └─ 可选 ─> ntfy ──> 手机 App
```

## 部署步骤

### 1. 准备目录与配置

```bash
mkdir -p im-desktop/{data,downloads,documents,pictures}
cd im-desktop
curl -O https://raw.githubusercontent.com/wune5/pim/main/docker-compose.yml
```

目录映射关系（compose 已配好）：

| 宿主机目录 | 容器内路径 | 用途 |
|---|---|---|
| `./data` | `/config` | 登录态、聊天记录、整个桌面 HOME |
| `./downloads` | `/config/Downloads` | 桌面「下载」文件夹，与宿主机互通 |
| `./documents` | `/config/Documents` | 桌面「文档」文件夹 |
| `./pictures` | `/config/Pictures` | 桌面「图片」文件夹 |

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
   否则中继收不到消息
4. 登录态保存在 `./data`，容器重启无需重新扫码

### 4. 收消息

- **推荐**：浏览器打开 `https://IP:3001/relay/`（和桌面共用同一个端口，
  首次接受证书后点一次「开启浏览器通知」），实时列表 + 系统级弹窗
- 或者 HTTP 直连 `http://IP:8787`：只有消息列表，弹窗受浏览器
  安全上下文限制不可用
- 手机：ntfy App 订阅同一 topic（见上），页面不开也能收
- 想回复消息：切回 `https://IP:3001` 桌面直接操作客户端

> 端口统一说明：3001 由内置的 Caddy 反代统一入口——`/` 是桌面，
> `/relay/` 是通知页面；webtop 自身的 3001/8443 和中继的 8443
> 都只在容器网络内部，不再直接对外暴露。

### 5. 验证通知链路

容器桌面终端里执行：

```bash
notify-send "微信" "张三: 测试消息"
```

`http://IP:8787` 页面立即出现这条即链路正常。收不到真实消息时，
先检查客户端的通知开关，再看容器日志 `docker compose logs im-desktop`。

### 6. 常用维护命令

```bash
docker compose pull && docker compose up -d   # 更新镜像（每周一自动出新版）
docker compose restart im-desktop             # 重启
tar czf data-backup.tgz data                  # 备份登录态
```

## 客户端能力

| 能力 | 支持情况 |
|---|---|
| 浏览器复制文本进容器 | ✅ 双向 |
| 浏览器贴图（如截图进微信） | ✅ 双向（需 HTTPS :3001） |
| 浏览器拖拽/下载文件 | ✅（侧边栏文件管理器） |
| 收到消息浏览器推送 | ✅（:8787，依赖客户端开启通知） |
| 回复消息 | 在浏览器桌面里直接操作客户端 |

## 镜像构建

`docker build` 时通过 build-args 传入三个客户端 deb 的下载地址
（按 `TARGETARCH` 自动选 amd64/arm64 包）：

```bash
docker build -t im-desktop . \
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

- 消息推送是**只读**的：仅收到通知里的文本摘要，回复需在浏览器桌面里操作客户端。
- 长消息会被客户端通知截断；图片/语音/文件只显示提示文字。
- 窗口聚焦时部分应用可能不发通知。
- QQ/微信/钉钉均为官方客户端本体，中继只旁听系统通知，不碰协议，无封号风险。

## 三个客户端的官方下载源

| 客户端 | 地址 | arm64 |
|---|---|---|
| QQ | https://im.qq.com/linuxqq/index.html | ✅ |
| 微信 | https://linux.weixin.qq.com/ | ✅（选「ARM 版」） |
| 钉钉 | https://www.dingtalk.com/ 下载页 | ✅ |
