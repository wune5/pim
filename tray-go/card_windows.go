//go:build windows

// QQ 式悬浮卡片: 无边框置顶窗口, 悬停托盘图标 350ms 弹出,
// 显示未读数与最近 10 条消息。点击卡片 = 清未读 + 打开桌面;
// 光标离开「卡片+托盘图标」区域自动隐藏(看门狗定时器)。
package main

import (
	"fmt"
	"strings"
	"sync"
	"syscall"
	"unsafe"
)

const (
	timerWatch = 2 // 卡片看门狗定时器

	cardW       = 360 // 卡片宽度(px)
	cardRowH    = 56  // 单条消息行高
	cardHeadH   = 42  // 头部高度
	cardMaxRows = 10
)

// COLORREF = 0x00BBGGRR
const (
	clBg     = 0xFFFFFF
	clBorder = 0xD8D8D8
	clTitle  = 0x222222
	clBody   = 0x555555
	clApp    = 0xD85241 // RGB(65,82,216) 应用名蓝
	clTime   = 0x989898
)

var card struct {
	mu        sync.Mutex
	hwnd      uintptr
	vis       bool
	x, y, w, h int32
	ax, ay    int32 // 弹出时光标位置(= 托盘图标锚点)
	fontHead, fontTitle, fontBody uintptr
	brushBg, penBorder            uintptr
}

var cardWndProcCb = syscall.NewCallback(cardWndProc)

func init() {
	uiCardRefresh = func() {
		card.mu.Lock()
		vis, hwnd := card.vis, card.hwnd
		card.mu.Unlock()
		if vis && hwnd != 0 {
			procPostMessageW.Call(hwnd, wmAppCard, 0, 0)
		}
	}
}

func cardToggle() {
	card.mu.Lock()
	vis := card.vis
	card.mu.Unlock()
	if vis {
		cardHide()
	} else {
		cardShow(getCursor())
	}
}

// cardShow 在托盘图标上方弹出卡片(仅当有未读时)
func cardShow(anchor point) {
	st.mu.Lock()
	unread := st.unread
	recent := append([]Message{}, st.recent...)
	st.mu.Unlock()
	if unread == 0 || len(recent) == 0 {
		return // 无未读不弹, 与 QQ 行为一致
	}
	if len(recent) > cardMaxRows {
		recent = recent[:cardMaxRows]
	}

	h := cardEnsure()
	w := int32(cardW)
	ht := int32(cardHeadH) + int32(cardRowH)*int32(len(recent)) + 10

	// 位置: 光标上方右对齐, 约束在主屏工作区内(任务栏在顶栏时放光标下方)
	var wa rect
	procSystemParametersInfoW.Call(0x0030 /*SPI_GETWORKAREA*/, 0, uintptr(unsafe.Pointer(&wa)), 0)
	x := anchor.X - w + 20
	y := anchor.Y - ht - 8
	if x < wa.L {
		x = wa.L
	}
	if x+w > wa.R {
		x = wa.R - w
	}
	if y < wa.T {
		y = anchor.Y + 24
	}

	card.mu.Lock()
	card.ax, card.ay = anchor.X, anchor.Y
	card.x, card.y, card.w, card.h = x, y, w, ht
	card.vis = true
	card.mu.Unlock()

	procSetWindowPos.Call(h, ^uintptr(0) /*HWND_TOPMOST*/,
		uintptr(x), uintptr(y), uintptr(w), uintptr(ht),
		0x0040 /*SWP_SHOWWINDOW*/)
	procShowWindow.Call(h, 4 /*SW_SHOWNOACTIVATE*/)
	procSetTimer.Call(h, timerWatch, 300, 0)
	procInvalidateRect.Call(h, 0, 0)
}

func cardHide() {
	card.mu.Lock()
	vis, hwnd := card.vis, card.hwnd
	card.vis = false
	card.mu.Unlock()
	if vis && hwnd != 0 {
		procKillTimer.Call(hwnd, timerWatch)
		procShowWindow.Call(hwnd, 0 /*SW_HIDE*/)
	}
}

// cardResize 消息数变化时重算高度并重绘(卡片开着时来新消息实时刷新)
func cardResize() {
	st.mu.Lock()
	recent := append([]Message{}, st.recent...)
	st.mu.Unlock()
	if len(recent) > cardMaxRows {
		recent = recent[:cardMaxRows]
	}
	ht := int32(cardHeadH) + int32(cardRowH)*int32(len(recent)) + 10
	card.mu.Lock()
	card.h = ht
	card.mu.Unlock()
	procSetWindowPos.Call(card.hwnd, 0,
		0, 0, uintptr(cardW), uintptr(ht),
		0x2|0x4 /*SWP_NOMOVE|SWP_NOZORDER*/)
	procInvalidateRect.Call(card.hwnd, 0, 0)
}

// cardWatchdog 每 300ms 检查: 光标离开「卡片+托盘图标」区域即隐藏
func cardWatchdog() {
	card.mu.Lock()
	vis := card.vis
	x, y, w, h := card.x, card.y, card.w, card.h
	ax, ay := card.ax, card.ay
	card.mu.Unlock()
	if !vis {
		return
	}
	pt := getCursor()
	inCard := pt.X >= x && pt.X < x+w && pt.Y >= y && pt.Y < y+h
	nearIcon := abs32(pt.X-ax) <= 48 && abs32(pt.Y-ay) <= 48
	if !inCard && !nearIcon {
		cardHide()
	}
}

// cardEnsure 懒创建卡片窗口与 GDI 资源(仅在 UI 线程调用)
func cardEnsure() uintptr {
	card.mu.Lock()
	h := card.hwnd
	card.mu.Unlock()
	if h != 0 {
		return h
	}

	hinst, _, _ := procGetModuleHandleW.Call(0)
	var wc wndClassEx
	wc.CbSize = uint32(unsafe.Sizeof(wc))
	wc.LpfnWndProc = cardWndProcCb
	wc.HInstance = hinst
	wc.HCursor, _, _ = procLoadCursorW.Call(0, 32512)
	cls, _ := syscall.UTF16PtrFromString("pim-tray-card")
	wc.LpszClassName = uintptr(unsafe.Pointer(cls))
	procRegisterClassExW.Call(uintptr(unsafe.Pointer(&wc)))

	h, _, _ = procCreateWindowExW.Call(
		0x08000000|0x80|0x8, // WS_EX_NOACTIVATE|WS_EX_TOOLWINDOW|WS_EX_TOPMOST
		uintptr(unsafe.Pointer(cls)), 0,
		0x80000000, // WS_POPUP (无边框)
		0, 0, 0, 0, 0, 0, hinst, 0)

	card.mu.Lock()
	card.hwnd = h
	card.fontHead = makeFont(-15, 700)
	card.fontTitle = makeFont(-14, 700)
	card.fontBody = makeFont(-13, 400)
	card.brushBg, _, _ = procCreateSolidBrush.Call(clBg)
	card.penBorder, _, _ = procCreatePen.Call(0 /*PS_SOLID*/, 1, clBorder)
	card.mu.Unlock()
	return h
}

func cardWndProc(hwnd uintptr, msg uint32, wp, lp uintptr) uintptr {
	switch msg {
	case wmPaint:
		paintCard(hwnd)
		return 0
	case wmAppCard:
		cardResize()
		return 0
	case wmTimer:
		if wp == timerWatch {
			cardWatchdog()
		}
		return 0
	case wmLButtonUp:
		cardHide()
		go func() {
			clearUnread()
			openURL(serverURL + "/")
		}()
		return 0
	}
	r, _, _ := procDefWindowProcW.Call(hwnd, uintptr(msg), wp, lp)
	return r
}

// paintCard GDI 绘制: 白底圆角卡片 + 头部未读数 + 消息列表
func paintCard(hwnd uintptr) {
	var ps paintStruct
	hdc, _, _ := procBeginPaint.Call(hwnd, uintptr(unsafe.Pointer(&ps)))
	defer procEndPaint.Call(hwnd, uintptr(unsafe.Pointer(&ps)))

	card.mu.Lock()
	w, h := card.w, card.h
	fontHead, fontTitle, fontBody := card.fontHead, card.fontTitle, card.fontBody
	brushBg, penBorder := card.brushBg, card.penBorder
	card.mu.Unlock()

	st.mu.Lock()
	unread := st.unread
	recent := append([]Message{}, st.recent...)
	st.mu.Unlock()
	if len(recent) > cardMaxRows {
		recent = recent[:cardMaxRows]
	}

	// 白底圆角 + 灰描边
	ob, _, _ := procSelectObject.Call(hdc, brushBg)
	op, _, _ := procSelectObject.Call(hdc, penBorder)
	procRoundRect.Call(hdc, 0, 0, uintptr(w), uintptr(h), 16, 16)
	procSelectObject.Call(hdc, ob)
	procSelectObject.Call(hdc, op)
	procSetBkMode.Call(hdc, 1) // TRANSPARENT

	// 头部
	drawText(hdc, fontHead, fmt.Sprintf("未读 %d 条", unread),
		16, 10, w-32, 24, clTitle)

	// 消息列表(最新在上)
	for i, m := range recent {
		y := int32(cardHeadH) + int32(i)*cardRowH
		drawText(hdc, fontTitle, rowTitle(m), 16, y+6, w-80, 20, clTitle)
		drawText(hdc, fontBody, rowTime(m.TS), w-62, y+8, 50, 18, clTime)
		drawText(hdc, fontBody, m.Body, 16, y+30, w-32, 18, clBody)
	}
}

// drawText 单行文本, 超宽自动省略号
func drawText(hdc, font uintptr, s string, x, y, w, h int32, color uint32) {
	procSelectObject.Call(hdc, font)
	procSetTextColor.Call(hdc, uintptr(color))
	r := rect{L: x, T: y, R: x + w, B: y + h}
	b := utf16Clip(s, 200)
	procDrawTextW.Call(hdc, utf16Ptr(b), ^uintptr(0) /*cchText=-1*/,
		uintptr(unsafe.Pointer(&r)),
		0x20|0x8000|0x800) // DT_SINGLELINE|DT_END_ELLIPSIS|DT_NOPREFIX
}

func rowTitle(m Message) string {
	switch {
	case m.Title != "" && m.App != "":
		return m.App + " · " + m.Title
	case m.Title != "":
		return m.Title
	case m.App != "":
		return m.App
	}
	return "新消息"
}

// rowTime 提取时间部分("11-27 11:02:15" → "11:02:15")
func rowTime(ts string) string {
	if i := strings.Index(ts, " "); i >= 0 {
		return ts[i+1:]
	}
	return ts
}
