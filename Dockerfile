# syntax=docker/dockerfile:1
# QQ / 微信 / 钉钉 桌面容器（amd64 + arm64），基于 LinuxServer webtop (Selkies)
FROM ghcr.io/linuxserver/webtop:ubuntu-kde

ARG TARGETARCH

# 三个客户端的 deb 下载地址（GitHub Actions 会用 scripts/resolve-urls.sh 解析最新版覆盖这些默认值）
ARG WECHAT_URL_AMD64=https://dldir1v6.qq.com/weixin/Universal/Linux/WeChatLinux_x86_64.deb
ARG WECHAT_URL_ARM64=https://dldir1v6.qq.com/weixin/Universal/Linux/WeChatLinux_arm64.deb
ARG QQ_URL_AMD64=https://qqdl.gtimg.cn/qqfile/QQNT/9.9.37/beta/816b3c79/linuxqq_3.2.35-54395_amd64.deb
ARG QQ_URL_ARM64=https://qqdl.gtimg.cn/qqfile/QQNT/9.9.37/beta/816b3c79/linuxqq_3.2.35-54395_arm64.deb
ARG DINGTALK_URL_AMD64=https://dtapp-pub.dingtalk.com/dingtalk-desktop/xc_dingtalk_update/linux_deb/Release/com.alibabainc.dingtalk_7.8.15.5092302_amd64.deb
ARG DINGTALK_URL_ARM64=https://dtapp-pub.dingtalk.com/dingtalk-desktop/xc_dingtalk_update/linux_deb/Release/com.alibabainc.dingtalk_7.8.15.5092302_arm64.deb

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl fonts-noto-cjk patchelf libpulse-mainloop-glib0 \
    && rm -rf /var/lib/apt/lists/*

# 按 TARGETARCH 选择对应架构的 deb 并安装
RUN case "$TARGETARCH" in \
      arm64) WECHAT_URL="$WECHAT_URL_ARM64"; QQ_URL="$QQ_URL_ARM64"; DT_URL="$DINGTALK_URL_ARM64" ;; \
      *)     WECHAT_URL="$WECHAT_URL_AMD64"; QQ_URL="$QQ_URL_AMD64"; DT_URL="$DINGTALK_URL_AMD64" ;; \
    esac \
    && curl -fL --retry 3 --progress-bar "$WECHAT_URL" -o /tmp/wechat.deb \
    && curl -fL --retry 3 --progress-bar "$QQ_URL" -o /tmp/qq.deb \
    && curl -fL --retry 3 --progress-bar "$DT_URL" -o /tmp/dingtalk.deb \
    && (dpkg -i /tmp/wechat.deb /tmp/qq.deb /tmp/dingtalk.deb || true) \
    && (apt-get update && apt-get -f install -y) \
    && rm -f /tmp/*.deb \
    && rm -rf /var/lib/apt/lists/*

# 容器内适配（实测 webtop ubuntu-kde 基础环境需要）：
# 1) QQ 是 Electron 应用，容器内 SUID/用户命名空间沙箱不可用，必须 --no-sandbox
# 2) 钉钉自带旧版 libm.so.6 会遮蔽系统 glibc(2.4x)，导致系统库加载失败，移除之
# 3) 钉钉 dingtalk_dll.so 带可执行栈标志，内核拒绝加载，清除该标志
# 4) 钉钉依赖 libpulse-mainloop-glib0（deb 里未声明）
RUN patchelf --clear-execstack /opt/apps/com.alibabainc.dingtalk/files/*/dingtalk_dll.so \
    && rm -f /opt/apps/com.alibabainc.dingtalk/files/*/libm.so.6 \
    && sed -i 's|Exec=/opt/QQ/qq %U|Exec=/opt/QQ/qq --no-sandbox %U|' /usr/share/applications/qq.desktop

# 启动时修复宿主机映射目录属主：bind mount 会保留宿主机 root 属主，
# 而容器内应用以 abc 用户运行，QQ 会报"检测到当前存储路径不可访问"
COPY pim-dirs-init/type /etc/s6-overlay/s6-rc.d/pim-dirs-init/type
COPY pim-dirs-init/up /etc/s6-overlay/s6-rc.d/pim-dirs-init/up
RUN chmod +x /etc/s6-overlay/s6-rc.d/pim-dirs-init/up \
    && echo "pim-dirs-init" >> /etc/s6-overlay/s6-rc.d/user/contents.d

# 通知中继：监听桌面通知 -> 浏览器页面推送（SSE，:8787）
COPY notify_relay.py /opt/notify_relay.py
RUN printf '[Desktop Entry]\nType=Application\nName=notify-relay\nComment=IM desktop notification relay\nExec=/lsiopy/bin/python3 /opt/notify_relay.py\nTerminal=false\nX-GNOME-Autostart-enabled=true\n' > /etc/xdg/autostart/notify-relay.desktop \
    && printf '[Desktop Entry]\nType=Application\nName=notify-relay\nExec=python3 /opt/notify_relay.py\nTerminal=false\n' > /usr/share/applications/notify-relay.desktop
