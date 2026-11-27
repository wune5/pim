// pim 托盘客户端 (Go 版) —— 接收 pim 容器的消息推送, 系统托盘常驻。
//
// 支持 Windows / macOS / Linux:
//   - 连接服务端 SSE(/notify/events), 实时消息弹系统通知 + 未读角标 + 图标闪烁
//   - backlog(历史消息)只累计未读不弹窗, 与网页端行为一致
//   - 悬浮 tooltip 显示未读消息摘要(QQ 式)
//   - 图标: 绿=在线, 暗灰=断开, 浅灰=暂停, 橙=闪烁高亮相
//
// 配置: 同目录 config.json (首次运行自动生成), 例如:
//
//	{"server": "https://192.168.20.19:3001", "verify_tls": false}
//
// 单文件静态编译, 无运行时依赖:
//	go build -ldflags "-s -w" -o pim-tray.exe .
package main

import (
	"bufio"
	"crypto/tls"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/gen2brain/beeep"
)

// ---------- 配置 ----------

type Config struct {
	Server    string `json:"server"`
	VerifyTLS bool   `json:"verify_tls"`
}

var (
	baseDir   = mustExeDir()
	cfgPath   = filepath.Join(baseDir, "config.json")
	logPath   = filepath.Join(baseDir, "tray.log")
	logMu     sync.Mutex
	cfg       = loadConfig()
	serverURL = strings.TrimRight(cfg.Server, "/")
	eventsURL = serverURL + "/notify/events"
)

func mustExeDir() string {
	exe, err := os.Executable()
	if err != nil {
		exe, _ = filepath.Abs(os.Args[0])
	}
	return filepath.Dir(exe)
}

func loadConfig() Config {
	var c Config
	if _, err := os.Stat(cfgPath); err != nil {
		c = Config{Server: "https://192.168.20.19:3001", VerifyTLS: false}
		b, _ := json.MarshalIndent(c, "", "  ")
		os.WriteFile(cfgPath, b, 0o644)
	}
	b, err := os.ReadFile(cfgPath)
	if err == nil {
		json.Unmarshal(b, &c)
	}
	if c.Server == "" {
		fmt.Fprintln(os.Stderr, "请编辑 config.json 填入服务器地址, 例如 https://192.168.20.19:3001")
		os.Exit(1)
	}
	return c
}

func logf(format string, args ...any) {
	logMu.Lock()
	defer logMu.Unlock()
	f, err := os.OpenFile(logPath, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		return
	}
	defer f.Close()
	fmt.Fprintf(f, "%s %s\n", time.Now().Format("15:04:05"), fmt.Sprintf(format, args...))
}

// ---------- 共享状态 ----------

type Message struct {
	App   string `json:"app"`
	Title string `json:"title"`
	Body  string `json:"body"`
	TS    string `json:"ts"`
}

type stateT struct {
	mu        sync.Mutex
	connected bool
	paused    bool
	unread    int
	blinkOn   bool
	recent    []Message // 最近消息, 供 tooltip 摘要 (最多 10 条)
}

var st stateT

// HTTP 客户端: 局域网工具不走系统代理; 自签名证书按配置放行
var httpClient = &http.Client{
	Transport: &http.Transport{
		Proxy:          nil, // 强制直连, 不读任何代理配置
		TLSClientConfig: &tls.Config{InsecureSkipVerify: !cfg.VerifyTLS},
	},
}

// ---------- 入口 ----------

func main() {
	if len(os.Args) > 1 && os.Args[1] == "--test-once" {
		testOnce()
		return
	}
	logf("=== pim-tray (go) start, server=%s ===", cfg.Server)
	uiRun(onReady)
}

func clearUnread() {
	st.mu.Lock()
	st.unread = 0
	st.blinkOn = false
	st.mu.Unlock()
	cardRefreshUI()
}

// ---------- 消息处理 ----------

func handle(m Message, backlog bool) {
	st.mu.Lock()
	paused := st.paused
	st.recent = append([]Message{m}, st.recent...)
	if len(st.recent) > 10 {
		st.recent = st.recent[:10]
	}
	st.mu.Unlock()

	if backlog {
		// 历史消息: 只累计未读, 不弹窗(与网页端一致)
		if !paused {
			st.mu.Lock()
			st.unread++
			st.mu.Unlock()
		}
	} else if paused {
		logf("realtime msg skipped (paused)")
	} else {
		title := m.Title
		if title == "" {
			title = m.App
		}
		if title == "" {
			title = "新消息"
		}
		go notify(m.App, title, m.Body)
		st.mu.Lock()
		st.unread++
		st.mu.Unlock()
	}
	logf("handled: backlog=%v paused=%v unread=%d", backlog, paused, unreadLocked())
	cardRefreshUI()
}

func unreadLocked() int {
	st.mu.Lock()
	defer st.mu.Unlock()
	return st.unread
}

// ---------- 系统通知 ----------

// notify 弹系统通知; app 作为来源应用名显示(Windows toast 头部/通知中心分组)
var notifyMu sync.Mutex

func notify(app, title, body string) {
	defer func() {
		if r := recover(); r != nil {
			logf("notify PANIC: %v", r)
		}
	}()
	if app == "" {
		app = "pim"
	}
	// beeep.AppName 是全局变量, 并发弹通知时可能串台, 串行化
	notifyMu.Lock()
	defer notifyMu.Unlock()
	beeep.AppName = app
	if err := beeep.Notify(title, body, ""); err != nil {
		logf("notify ERROR: %v", err)
	} else {
		logf("toast OK: [%s] %s | %s", app, title, clip(body, 30))
	}
}

// ---------- SSE ----------

func sseLoop(refreshUI func()) {
	backoff := 2 * time.Second
	logf("sse_loop start -> %s", eventsURL)
	for {
		err := sseOnce(refreshUI)
		st.mu.Lock()
		st.connected = false
		st.mu.Unlock()
		refreshUI()
		if err != nil {
			logf("SSE error: %v; retry in %v", err, backoff)
		}
		time.Sleep(backoff)
		backoff = min(backoff*2, 60*time.Second)
		if err == nil {
			backoff = 2 * time.Second
		}
	}
}

func sseOnce(refreshUI func()) error {
	req, err := http.NewRequest("GET", eventsURL, nil)
	if err != nil {
		return err
	}
	req.Header.Set("Accept", "text/event-stream")
	resp, err := httpClient.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("HTTP %d", resp.StatusCode)
	}
	st.mu.Lock()
	st.connected = true
	st.mu.Unlock()
	logf("SSE connected HTTP %d", resp.StatusCode)
	refreshUI()

	sc := bufio.NewScanner(resp.Body)
	sc.Buffer(make([]byte, 64*1024), 1024*1024)
	event := "message"
	for sc.Scan() {
		line := sc.Text()
		switch {
		case line == "":
			event = "message"
		case strings.HasPrefix(line, "event:"):
			event = strings.TrimSpace(line[6:])
		case strings.HasPrefix(line, "data:"):
			var m Message
			if json.Unmarshal([]byte(strings.TrimSpace(line[5:])), &m) == nil {
				logf("event %s: %s %s | %s", event, m.App, m.Title, clip(m.Body, 20))
				handle(m, event == "backlog")
				refreshUI()
			}
		}
	}
	return sc.Err()
}

func testOnce() {
	req, _ := http.NewRequest("GET", eventsURL, nil)
	resp, err := httpClient.Do(req)
	if err != nil {
		fmt.Println("ERROR", err)
		os.Exit(1)
	}
	defer resp.Body.Close()
	fmt.Println("HTTP", resp.StatusCode)
	sc := bufio.NewScanner(resp.Body)
	n, ev := 0, ""
	for sc.Scan() {
		line := sc.Text()
		if strings.HasPrefix(line, "event:") {
			ev = strings.TrimSpace(line[6:])
			fmt.Println("EVENT", ev)
			continue
		}
		if strings.HasPrefix(line, "data:") {
			fmt.Println("DATA", clip(strings.TrimSpace(line[5:]), 120))
			if n++; n >= 3 {
				break
			}
		}
	}
}

// ---------- tooltip ----------

func clip(s string, n int) string {
	s = strings.NewReplacer("\r", " ", "\n", " ").Replace(s)
	s = strings.TrimSpace(s)
	r := []rune(s)
	if len(r) <= n {
		return s
	}
	return string(r[:max(n-1, 1)]) + "…"
}

func buildTooltip() string {
	st.mu.Lock()
	connected, paused, unread := st.connected, st.paused, st.unread
	recent := append([]Message{}, st.recent...)
	st.mu.Unlock()

	status := "已暂停"
	switch {
	case paused:
	case connected:
		status = "已连接"
	default:
		status = "连接断开, 重试中…"
	}
	head := fmt.Sprintf("pim 消息提醒 (%s)", status)
	if unread == 0 {
		return head
	}
	lines := []string{fmt.Sprintf("%s | 未读 %d 条:", head, unread)}
	for i, m := range recent {
		if i >= 3 {
			break
		}
		lines = append(lines, fmt.Sprintf("%s %s: %s",
			clip(m.App, 8), clip(m.Title, 12), clip(m.Body, 18)))
	}
	var b strings.Builder
	total := 0
	for _, l := range lines {
		l = clip(l, 40)
		if total+len(l) > 120 {
			break
		}
		if b.Len() > 0 {
			b.WriteByte('\n')
		}
		b.WriteString(l)
		total += len(l) + 1
	}
	return b.String()
}

// ---------- 工具 ----------

func openURL(u string) {
	var cmd *exec.Cmd
	switch runtime.GOOS {
	case "windows":
		cmd = exec.Command("rundll32", "url.dll,FileProtocolHandler", u)
	case "darwin":
		cmd = exec.Command("open", u)
	default:
		cmd = exec.Command("xdg-open", u)
	}
	_ = cmd.Start()
}

func itoa(n int) string {
	return strconv.Itoa(n)
}
