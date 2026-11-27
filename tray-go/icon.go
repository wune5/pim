// 托盘图标绘制: 64x64 PNG, 纯标准库, 无字体文件依赖。
// 底色=状态色, 中央白色 "P", 右上角红色未读角标(数字)。
// 统一返回 PNG 字节: 非 Windows 平台 systray 直接可用,
// Windows 端由 tray_windows.go 用 CreateIconFromResourceEx 加载。
package main

import (
	"bytes"
	"image"
	"image/color"
	"image/png"
)

// 5x7 位图字体(所需字符: P 0-9 +)
var glyph5x7 = map[byte][7]string{
	'P': {"11110", "10001", "10001", "11110", "10000", "10000", "10000"},
	'0': {"01110", "10001", "10011", "10101", "11001", "10001", "01110"},
	'1': {"00100", "01100", "00100", "00100", "00100", "00100", "01110"},
	'2': {"01110", "10001", "00001", "00110", "01000", "10000", "11111"},
	'3': {"11111", "00010", "00100", "00010", "00001", "10001", "01110"},
	'4': {"00010", "00110", "01010", "10010", "11111", "00010", "00010"},
	'5': {"11111", "10000", "11110", "00001", "00001", "10001", "01110"},
	'6': {"00110", "01000", "10000", "11110", "10001", "10001", "01110"},
	'7': {"11111", "00001", "00010", "00100", "01000", "01000", "01000"},
	'8': {"01110", "10001", "10001", "01110", "10001", "10001", "01110"},
	'9': {"01110", "10001", "10001", "01111", "00001", "00010", "01100"},
	'+': {"00000", "00100", "00100", "11111", "00100", "00100", "00000"},
}

var (
	cOnline    = color.RGBA{34, 197, 94, 255}   // 绿: 在线
	cBlink     = color.RGBA{251, 146, 60, 255}  // 橙: 闪烁高亮相
	cOffline   = color.RGBA{75, 85, 99, 255}    // 暗灰: 连接断开
	cPaused    = color.RGBA{148, 163, 184, 255} // 浅灰: 暂停
	cBadge     = color.RGBA{220, 38, 38, 255}   // 角标红
	cWhite     = color.RGBA{255, 255, 255, 255}
)

// drawIcon 根据当前状态绘制托盘图标(PNG 字节)
func drawIcon() []byte {
	st.mu.Lock()
	connected, paused, unread, blinkOn := st.connected, st.paused, st.unread, st.blinkOn
	st.mu.Unlock()

	blinking := unread > 0 && !paused && connected
	var base color.RGBA
	switch {
	case blinking && blinkOn:
		base = cBlink
	case !connected:
		base = cOffline
	case paused:
		base = cPaused
	default:
		base = cOnline
	}

	img := image.NewRGBA(image.Rect(0, 0, 64, 64))
	roundedRect(img, 4, 8, 60, 60, 14, base)
	// 白色 "P": 5x7 字形放大 2 倍 = 10x14, 居中偏上
	drawGlyph(img, 'P', 27, 25, 2, cWhite)
	// 未读角标
	if unread > 0 && !paused {
		n := itoa(unread)
		if unread > 99 {
			n = "99+"
		}
		badge(img, n)
	}

	var buf bytes.Buffer
	if err := png.Encode(&buf, img); err != nil {
		return nil
	}
	return buf.Bytes()
}

// roundedRect 圆角矩形(左上/右下角坐标 + 圆角半径)
func roundedRect(img *image.RGBA, x0, y0, x1, y1, r int, c color.RGBA) {
	for y := y0; y <= y1; y++ {
		for x := x0; x <= x1; x++ {
			if !inRounded(x, y, x0, y0, x1, y1, r) {
				continue
			}
			img.Set(x, y, c)
		}
	}
}

func inRounded(x, y, x0, y0, x1, y1, r int) bool {
	cx, cy := x0+r, y0+r   // 左上角圆心
	if x > x0+r && y > y1-r {
		cx, cy = x0+r, y1-r
	}
	if x < x1-r && y > y1-r {
		cx, cy = x1-r, y1-r
	}
	if x < x1-r && y < y0+r {
		cx, cy = x1-r, y0+r
	}
	corner := (x < x0+r || x > x1-r) && (y < y0+r || y > y1-r)
	if corner {
		dx, dy := x-cx, y-cy
		return dx*dx+dy*dy <= r*r
	}
	return true
}

// drawGlyph 绘制 5x7 字形, scale 倍放大, (x,y) 为左上角
func drawGlyph(img *image.RGBA, ch byte, x, y, scale int, c color.RGBA) {
	g, ok := glyph5x7[ch]
	if !ok {
		return
	}
	for row, line := range g {
		for col := 0; col < 5; col++ {
			if line[col] != '1' {
				continue
			}
			for dy := 0; dy < scale; dy++ {
				for dx := 0; dx < scale; dx++ {
					img.Set(x+col*scale+dx, y+row*scale+dy, c)
				}
			}
		}
	}
}

// badge 右上角红色圆 + 白色数字
func badge(img *image.RGBA, n string) {
	cx, cy := 50, 12
	scale := 2
	if len(n) > 2 {
		scale = 1
	}
	// 字形总宽: 每字符 5*scale + 1*scale 间距
	totalW := len(n)*6*scale - scale
	textH := 7 * scale
	// 角标半径: 覆盖文本再加边距
	r := max(totalW, textH)/2 + 5
	for y := cy - r - 1; y <= cy+r+1; y++ {
		for x := cx - r - 1; x <= cx+r+1; x++ {
			dx, dy := x-cx, y-cy
			d2 := dx*dx + dy*dy
			if d2 <= r*r && d2 > (r-2)*(r-2) {
				img.Set(x, y, cWhite) // 白描边
			} else if d2 <= (r-2)*(r-2) {
				img.Set(x, y, cBadge)
			}
		}
	}
	tx := cx - totalW/2
	ty := cy - textH/2
	for i := 0; i < len(n); i++ {
		drawGlyph(img, n[i], tx+i*6*scale, ty, scale, cWhite)
	}
}
