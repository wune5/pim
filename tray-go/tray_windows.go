//go:build windows

// Windows 自研托盘: Shell_NotifyIcon + 隐藏窗口收鼠标事件。
// 左键 = 开关悬浮卡片, 右键 = 原生菜单, 悬停 350ms = 弹出消息卡片。
// 替代 fyne.io/systray 的 Windows 后端(systray 不暴露 hover 事件, 做不了 QQ 式悬浮卡片)。
package main

import (
	"runtime"
	"sync"
	"syscall"
	"unsafe"
)

const (
	timerHover = 1 // 托盘图标悬停定时器
)

// winItem 菜单项; sep=true 表示分隔线; onClick=nil 表示灰显状态行
type winItem struct {
	sep     bool
	grayed  bool
	onClick func()
	title   string
}

var tray struct {
	mu       sync.Mutex
	hwnd     uintptr
	nid      notifyIconData
	hIcon    uintptr
	pendIcon []byte
	pendTip  string
	items    []winItem // 按添加顺序; TrackPopupMenu 的返回值 = 下标+1
}

// 回调必须存包级变量, 防止被 GC 回收后窗口过程失效
var trayWndProcCb = syscall.NewCallback(trayWndProc)

func init() {
	uiRun = winRun
	uiSetIcon = winSetIcon
	uiSetTooltip = winSetTooltip
	uiSetTitle = func(string) {} // 托盘标题只有 macOS 渲染, Windows 忽略
	uiQuit = winQuit
	uiAddItem = winAddItem
	uiAddSeparator = winAddSeparator
	uiDisable = func(r menuRef) { r.disable() }
}

func winRun(onReady func()) {
	runtime.LockOSThread() // 消息循环/GDI 需要绑定主线程

	hinst, _, _ := procGetModuleHandleW.Call(0)
	var wc wndClassEx
	wc.CbSize = uint32(unsafe.Sizeof(wc))
	wc.LpfnWndProc = trayWndProcCb
	wc.HInstance = hinst
	wc.HCursor, _, _ = procLoadCursorW.Call(0, 32512) // IDC_ARROW
	cls, _ := syscall.UTF16PtrFromString("pim-tray-host")
	wc.LpszClassName = uintptr(unsafe.Pointer(cls))
	procRegisterClassExW.Call(uintptr(unsafe.Pointer(&wc)))

	// 仅接收消息的隐藏窗口(不显示)
	hwnd, _, _ := procCreateWindowExW.Call(0,
		uintptr(unsafe.Pointer(cls)), 0, 0,
		0, 0, 0, 0,
		^uintptr(2), // HWND_MESSAGE
		0, hinst, 0)

	tray.mu.Lock()
	tray.hwnd = hwnd
	tray.nid.CbSize = uint32(unsafe.Sizeof(tray.nid))
	tray.nid.HWnd = hwnd
	tray.nid.UId = 1
	tray.nid.UFlags = nifAll
	tray.nid.UCallbackMessage = wmTray
	tray.mu.Unlock()
	procShellNotifyIconW.Call(nimAdd, uintptr(unsafe.Pointer(&tray.nid)))

	onReady()

	var m msgT
	for {
		r, _, _ := procGetMessageW.Call(uintptr(unsafe.Pointer(&m)), 0, 0, 0)
		if r == 0 || r == ^uintptr(0) { // WM_QUIT 或错误
			break
		}
		procTranslateMessage.Call(uintptr(unsafe.Pointer(&m)))
		procDispatchMessageW.Call(uintptr(unsafe.Pointer(&m)))
	}
	logf("tray exit")
}

func trayWndProc(hwnd uintptr, msg uint32, wp, lp uintptr) uintptr {
	switch msg {
	case wmTray:
		switch lp {
		case wmMouseMove:
			// 悬停: 每次移动都重置 350ms 定时器, 停下来才触发弹卡
			lastHover = getCursor()
			procSetTimer.Call(hwnd, timerHover, 350, 0)
		case wmLButtonUp:
			cardToggle()
		case wmRButtonUp:
			showTrayMenu(hwnd)
		}
		return 0
	case wmApply:
		applyPending()
		return 0
	case wmTimer:
		if wp == timerHover {
			procKillTimer.Call(hwnd, timerHover)
			hoverShowCard()
		}
		return 0
	case wmClose:
		procShellNotifyIconW.Call(nimDelete, uintptr(unsafe.Pointer(&tray.nid)))
		tray.mu.Lock()
		hIcon := tray.hIcon
		tray.mu.Unlock()
		if hIcon != 0 {
			procDestroyIcon.Call(hIcon)
		}
		procDestroyWindow.Call(hwnd)
		return 0
	case wmDestroy:
		procPostQuitMessage.Call(0)
		return 0
	}
	r, _, _ := procDefWindowProcW.Call(hwnd, uintptr(msg), wp, lp)
	return r
}

var lastHover point

// hoverShowCard 定时器到期: 光标仍停在图标附近才弹卡(防移开后误触发)
func hoverShowCard() {
	c := getCursor()
	if abs32(c.X-lastHover.X) <= 24 && abs32(c.Y-lastHover.Y) <= 24 {
		cardShow(c)
	}
}

// applyPending 把跨线程提交的图标/tooltip 更新应用到托盘(必须在 UI 线程执行)
func applyPending() {
	tray.mu.Lock()
	icon := tray.pendIcon
	tray.pendIcon = nil
	tip := tray.pendTip
	tray.pendTip = ""
	tray.mu.Unlock()

	changed := false
	if len(icon) > 4 {
		tray.mu.Lock()
		if tray.hIcon != 0 {
			procDestroyIcon.Call(tray.hIcon)
		}
		tray.hIcon = pngToHIcon(icon)
		tray.nid.HIcon = tray.hIcon
		tray.mu.Unlock()
		changed = true
	}
	if tip != "" {
		tray.mu.Lock()
		copy(tray.nid.SzTip[:], utf16Clip(tip, 127))
		tray.mu.Unlock()
		changed = true
	}
	if changed {
		procShellNotifyIconW.Call(nimModify, uintptr(unsafe.Pointer(&tray.nid)))
	}
}

// pngToHIcon PNG 字节 → HICON(Vista+ 支持 PNG 图标资源, 无需 ICO 容器)
func pngToHIcon(png []byte) uintptr {
	h, _, _ := procCreateIconFromResourceEx.Call(
		uintptr(unsafe.Pointer(&png[0])), uintptr(len(png)),
		1,        // fIcon = TRUE
		0x30000,  // dwVer = 0x00030000
		0, 0,     // 尺寸自适应
		0x40)     // LR_DEFAULTSIZE
	return h
}

func winSetIcon(b []byte) {
	tray.mu.Lock()
	tray.pendIcon = b
	tray.mu.Unlock()
	if tray.hwnd != 0 {
		procPostMessageW.Call(tray.hwnd, wmApply, 0, 0)
	}
}

func winSetTooltip(s string) {
	tray.mu.Lock()
	tray.pendTip = s
	tray.mu.Unlock()
	if tray.hwnd != 0 {
		procPostMessageW.Call(tray.hwnd, wmApply, 0, 0)
	}
}

func winQuit() {
	if tray.hwnd != 0 {
		procPostMessageW.Call(tray.hwnd, wmClose, 0, 0)
	}
}

func winAddItem(title, tip string, onClick func()) menuRef {
	tray.mu.Lock()
	idx := len(tray.items)
	tray.items = append(tray.items, winItem{grayed: onClick == nil, onClick: onClick, title: title})
	tray.mu.Unlock()
	return menuRef{
		set: func(s string) {
			tray.mu.Lock()
			tray.items[idx].title = s
			tray.mu.Unlock()
		},
		disable: func() {},
	}
}

func winAddSeparator() {
	tray.mu.Lock()
	tray.items = append(tray.items, winItem{sep: true})
	tray.mu.Unlock()
}

// showTrayMenu 右键原生菜单(每次现场构建, 标题总是最新)
func showTrayMenu(hwnd uintptr) {
	tray.mu.Lock()
	h, _, _ := procCreatePopupMenu.Call()
	for i, it := range tray.items {
		switch {
		case it.sep:
			procAppendMenuW.Call(h, 0x800 /*MF_SEPARATOR*/, 0, 0)
		case it.grayed:
			t := utf16Clip(it.title, 60)
			procAppendMenuW.Call(h, 0x3 /*MF_GRAYED|MF_DISABLED*/, 0, utf16Ptr(t))
		default:
			t := utf16Clip(it.title, 60)
			procAppendMenuW.Call(h, 0 /*MF_STRING*/, uintptr(i+1), utf16Ptr(t))
		}
	}
	tray.mu.Unlock()

	pt := getCursor()
	// 必须先设前台窗口, 否则点击外部无法正确关闭菜单(经典 Win32 坑)
	procSetForegroundWindow.Call(hwnd)
	ret, _, _ := procTrackPopupMenu.Call(h,
		0x0100|0x0020, // TPM_RETURNCMD|TPM_BOTTOMALIGN
		uintptr(pt.X), uintptr(pt.Y), hwnd, 0, 0)
	procPostMessageW.Call(hwnd, wmNull, 0, 0)
	procDestroyMenu.Call(h)

	tray.mu.Lock()
	var f func()
	if ret > 0 && int(ret) <= len(tray.items) {
		f = tray.items[ret-1].onClick
	}
	tray.mu.Unlock()
	if f != nil {
		go f() // 回调不能阻塞 UI 线程
	}
}
