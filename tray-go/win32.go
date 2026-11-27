//go:build windows

// Win32 syscall 封装: 纯标准库, 无 cgo, 无第三方依赖。
package main

import (
	"syscall"
	"unsafe"
)

var (
	user32   = syscall.NewLazyDLL("user32.dll")
	shell32  = syscall.NewLazyDLL("shell32.dll")
	gdi32    = syscall.NewLazyDLL("gdi32.dll")
	kernel32 = syscall.NewLazyDLL("kernel32.dll")
)

var (
	procGetModuleHandleW         = kernel32.NewProc("GetModuleHandleW")
	procRegisterClassExW         = user32.NewProc("RegisterClassExW")
	procCreateWindowExW          = user32.NewProc("CreateWindowExW")
	procDefWindowProcW           = user32.NewProc("DefWindowProcW")
	procGetMessageW              = user32.NewProc("GetMessageW")
	procTranslateMessage         = user32.NewProc("TranslateMessage")
	procDispatchMessageW         = user32.NewProc("DispatchMessageW")
	procPostQuitMessage          = user32.NewProc("PostQuitMessage")
	procPostMessageW             = user32.NewProc("PostMessageW")
	procDestroyWindow            = user32.NewProc("DestroyWindow")
	procLoadCursorW              = user32.NewProc("LoadCursorW")
	procSetTimer                 = user32.NewProc("SetTimer")
	procKillTimer                = user32.NewProc("KillTimer")
	procGetCursorPos             = user32.NewProc("GetCursorPos")
	procShowWindow               = user32.NewProc("ShowWindow")
	procSetWindowPos             = user32.NewProc("SetWindowPos")
	procInvalidateRect           = user32.NewProc("InvalidateRect")
	procBeginPaint               = user32.NewProc("BeginPaint")
	procEndPaint                 = user32.NewProc("EndPaint")
	procDrawTextW                = user32.NewProc("DrawTextW")
	procSetBkMode                = gdi32.NewProc("SetBkMode")
	procSetTextColor             = gdi32.NewProc("SetTextColor")
	procSelectObject             = gdi32.NewProc("SelectObject")
	procCreateFontW              = gdi32.NewProc("CreateFontW")
	procCreateSolidBrush         = gdi32.NewProc("CreateSolidBrush")
	procCreatePen                = gdi32.NewProc("CreatePen")
	procRoundRect                = gdi32.NewProc("RoundRect")
	procCreatePopupMenu          = user32.NewProc("CreatePopupMenu")
	procAppendMenuW              = user32.NewProc("AppendMenuW")
	procTrackPopupMenu           = user32.NewProc("TrackPopupMenu")
	procDestroyMenu              = user32.NewProc("DestroyMenu")
	procSetForegroundWindow      = user32.NewProc("SetForegroundWindow")
	procSystemParametersInfoW    = user32.NewProc("SystemParametersInfoW")
	procShellNotifyIconW         = shell32.NewProc("Shell_NotifyIconW")
	procCreateIconFromResourceEx = user32.NewProc("CreateIconFromResourceEx")
	procDestroyIcon              = user32.NewProc("DestroyIcon")
)

// 窗口消息
const (
	wmDestroy   = 0x0002
	wmClose     = 0x0010
	wmPaint     = 0x000F
	wmCommand   = 0x0111
	wmTimer     = 0x0113
	wmMouseMove = 0x0200
	wmLButtonUp = 0x0202
	wmRButtonUp = 0x0205
	wmNull      = 0x0000
	// WM_APP 段自定义消息
	wmTray    = 0x8000 + 1 // 托盘图标鼠标事件
	wmApply   = 0x8000 + 2 // 应用待更新的图标/tooltip
	wmAppCard = 0x8000 + 3 // 悬浮卡片刷新
)

// Shell_NotifyIcon
const (
	nimAdd    = 0
	nimModify = 1
	nimDelete = 2
	nifAll    = 0x7 // NIF_MESSAGE|NIF_ICON|NIF_TIP
)

type point struct{ X, Y int32 }

type rect struct{ L, T, R, B int32 }

type msgT struct {
	Hwnd    uintptr
	Message uint32
	Wparam  uintptr
	Lparam  uintptr
	Time    uint32
	Pt      point
}

// 字段布局与 winuser.h 的 WNDCLASSEXW / PAINTSTRUCT / NOTIFYICONDATAW 对齐(x64)
type wndClassEx struct {
	CbSize, Style        uint32
	LpfnWndProc          uintptr
	CbClsExtra, CbWndExtra int32
	HInstance, HIcon, HCursor, HbrBackground uintptr
	LpszMenuName, LpszClassName uintptr
	HIconSm              uintptr
}

type paintStruct struct {
	Hdc        uintptr
	FErase     int32
	RcPaint    rect
	FRestore   int32
	FIncUpdate int32
	Reserved   [32]byte
}

type notifyIconData struct {
	CbSize           uint32
	HWnd             uintptr
	UId              uint32
	UFlags           uint32
	UCallbackMessage uint32
	HIcon            uintptr
	SzTip            [128]uint16
	DwState          uint32
	DwStateMask      uint32
	SzInfo           [128]uint16
	UVersion         uint32
	SzInfoTitle      [64]uint16
	DwInfoFlags      uint32
	GuidItem         [16]byte
	HBalloonIcon     uintptr
}

// utf16Clip 字符串转 UTF-16(含结尾 NUL), 超过 n 个 rune 截断
func utf16Clip(s string, n int) []uint16 {
	r := []rune(s)
	if len(r) > n {
		r = r[:n]
	}
	b := make([]uint16, len(r)+1)
	for i, c := range r {
		b[i] = uint16(c)
	}
	return b
}

func utf16Ptr(b []uint16) uintptr {
	return uintptr(unsafe.Pointer(&b[0]))
}

func getCursor() point {
	var pt point
	procGetCursorPos.Call(uintptr(unsafe.Pointer(&pt)))
	return pt
}

func abs32(v int32) int32 {
	if v < 0 {
		return -v
	}
	return v
}

// makeFont 创建 GDI 字体(微软雅黑), h 为负值表示字符高度
func makeFont(h int32, weight int32) uintptr {
	face, _ := syscall.UTF16PtrFromString("Microsoft YaHei UI")
	hf, _, _ := procCreateFontW.Call(
		uintptr(h), 0, 0, 0, uintptr(weight), 0, 0, 0,
		1, // DEFAULT_CHARSET
		0, 0,
		5, // CLEARTYPE_QUALITY
		0, uintptr(unsafe.Pointer(face)))
	return hf
}
