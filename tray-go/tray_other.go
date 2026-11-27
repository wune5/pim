//go:build !windows

// macOS / Linux 托盘: fyne.io/systray 兜底实现。
// 这两个平台拿不到托盘 hover 事件(AppIndicator 协议无此概念),
// QQ 式悬浮卡片只在 Windows 提供, 此处保持 tooltip 摘要 + 菜单行为。
package main

import "fyne.io/systray"

func init() {
	uiRun = func(onReady func()) {
		systray.Run(onReady, func() { logf("tray exit") })
	}
	uiSetIcon = systray.SetIcon
	uiSetTooltip = systray.SetTooltip
	uiSetTitle = systray.SetTitle
	uiQuit = systray.Quit
	uiAddSeparator = systray.AddSeparator
	uiCardRefresh = func() {} // 无卡片
	uiAddItem = func(title, tip string, onClick func()) menuRef {
		it := systray.AddMenuItem(title, tip)
		if onClick != nil {
			go func() {
				for range it.ClickedCh {
					onClick()
				}
			}()
		}
		return menuRef{set: it.SetTitle, disable: it.Disable}
	}
	uiDisable = func(r menuRef) { r.disable() }
}
