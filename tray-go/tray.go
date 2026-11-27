// 托盘 UI 抽象层: Windows 用自研实现(tray_windows.go + card_windows.go),
// 其余平台用 systray(tray_other.go)。onReady 只面向这些钩子编码,
// 保证三平台共用同一套状态机/SSE/菜单逻辑。
package main

import (
	"fmt"
	"time"
)

// menuRef 菜单项句柄: set 更新标题, disable 置灰
type menuRef struct {
	set     func(string)
	disable func()
}

// 各平台实现注入的钩子(见 tray_windows.go / tray_other.go 的 init)
var (
	uiRun          func(onReady func())
	uiSetIcon      func(b []byte)
	uiSetTooltip   func(s string)
	uiSetTitle     func(s string)
	uiQuit         func()
	uiAddItem      func(title, tip string, onClick func()) menuRef
	uiAddSeparator func()
	uiDisable      func(r menuRef)
	uiCardRefresh  func() // 悬浮卡片开着时刷新内容(仅 Windows 实现)
)

// cardRefreshUI 通知悬浮卡片重绘(卡片隐藏时各实现内部自动 no-op)
func cardRefreshUI() {
	if uiCardRefresh != nil {
		uiCardRefresh()
	}
}

func onReady() {
	uiSetIcon(drawIcon())
	uiSetTooltip("pim 消息提醒")
	uiSetTitle("pim")

	var refreshUI func()

	mStatus := uiAddItem("", "", nil)
	uiDisable(mStatus)
	mUnread := uiAddItem("", "", nil)
	uiDisable(mUnread)
	uiAddSeparator()
	uiAddItem("打开网页桌面", "在浏览器打开 pim 桌面", func() {
		clearUnread()
		openURL(serverURL + "/")
	})
	mPause := uiAddItem("暂停提醒", "暂停/恢复系统通知", func() {
		st.mu.Lock()
		st.paused = !st.paused
		st.mu.Unlock()
		refreshUI()
	})
	uiAddItem("清除未读", "清除未读计数并停止闪烁", func() {
		clearUnread()
		refreshUI()
	})
	uiAddSeparator()
	uiAddItem("退出", "退出托盘程序", func() { uiQuit() })

	refreshUI = func() {
		st.mu.Lock()
		connected, paused, unread := st.connected, st.paused, st.unread
		st.mu.Unlock()
		status := "已暂停"
		switch {
		case paused:
		case connected:
			status = "已连接 " + cfg.Server
		default:
			status = "连接断开, 重试中…"
		}
		mStatus.set("状态: " + status)
		if unread > 0 {
			mUnread.set(fmt.Sprintf("未读: %d", unread))
		} else {
			mUnread.set("无未读")
		}
		if paused {
			mPause.set("恢复提醒")
		} else {
			mPause.set("暂停提醒")
		}
		uiSetIcon(drawIcon())
		uiSetTooltip(buildTooltip())
		cardRefreshUI()
	}
	refreshUI()

	// 未读闪烁: QQ 式, 不点不停
	go func() {
		for range time.Tick(500 * time.Millisecond) {
			st.mu.Lock()
			active := st.unread > 0 && !st.paused && st.connected
			if active {
				st.blinkOn = !st.blinkOn
			} else if st.blinkOn {
				st.blinkOn = false
			}
			need := active || st.blinkOn
			st.mu.Unlock()
			if need {
				refreshUI()
			}
		}
	}()

	go func() {
		defer func() {
			if r := recover(); r != nil {
				logf("sse PANIC: %v", r)
			}
		}()
		sseLoop(refreshUI)
	}()
}
