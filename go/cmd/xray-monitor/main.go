package main

import (
	"context"
	"flag"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/graynextry/xray-monitor/internal/tui"
)

const version = "1.0.0"

func main() {
	var (
		server     = flag.String("server", "127.0.0.1:10085", "Xray gRPC stats API address")
		configPath = flag.String("config", "/usr/local/etc/xray/config.json", "Path to Xray config.json")
		logPath    = flag.String("log", "/var/log/xray/access.log", "Path to Xray access.log")
		dataPath   = flag.String("data", defaultDataPath(), "Path to traffic history JSON file")
		interval   = flag.Float64("interval", 2.0, "Stats polling interval in seconds")
		serverIP   = flag.String("server-ip", "", "Server public IP (auto-detected if empty)")
		showVer    = flag.Bool("version", false, "Print version and exit")
	)
	flag.Parse()

	if *showVer {
		fmt.Println("xray-monitor", version)
		os.Exit(0)
	}

	log.SetFlags(0)

	// Auto-detect public IP if not provided
	ip := *serverIP
	if ip == "" {
		ip = detectPublicIP()
	}

	opts := tui.Options{
		Server:     *server,
		Interval:   time.Duration(*interval * float64(time.Second)),
		LogPath:    *logPath,
		ConfigPath: *configPath,
		DataPath:   *dataPath,
		ServerIP:   ip,
	}

	model, err := tui.NewModel(opts)
	if err != nil {
		log.Fatalf("init: %v", err)
	}

	p := tea.NewProgram(
		model,
		tea.WithAltScreen(),
		tea.WithMouseCellMotion(),
	)
	if _, err := p.Run(); err != nil {
		log.Fatalf("tui: %v", err)
	}
}

func defaultDataPath() string {
	if env := os.Getenv("XRAY_MONITOR_DATA"); env != "" {
		return env
	}
	return "/opt/xray-monitor/traffic_history.json"
}

func detectPublicIP() string {
	endpoints := []string{
		"https://api.ipify.org",
		"https://ifconfig.me/ip",
		"https://icanhazip.com",
	}
	ctx, cancel := context.WithTimeout(context.Background(), 4*time.Second)
	defer cancel()

	client := &http.Client{Timeout: 4 * time.Second}
	for _, url := range endpoints {
		req, _ := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
		resp, err := client.Do(req)
		if err != nil {
			continue
		}
		body, _ := io.ReadAll(io.LimitReader(resp.Body, 64))
		resp.Body.Close()
		ip := strings.TrimSpace(string(body))
		if ip != "" && len(ip) < 50 {
			return ip
		}
	}
	return ""
}
