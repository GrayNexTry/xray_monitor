// Package xray manages the Xray VPN process via systemctl and GitHub releases.
package xray

import (
	"archive/zip"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"strings"
	"sync"
	"time"
)

// Status describes the current state of the xray service.
type Status struct {
	Running bool
	Enabled bool
	PID     int
	Memory  int64
	Version string
	Uptime  string
}

type versionCache struct {
	tag string
	url string
	at  time.Time
}

// Manager controls the Xray VPN process.
type Manager struct {
	mu      sync.Mutex
	verCache versionCache
}

// New creates a Manager.
func New() *Manager { return &Manager{} }

// FindBinary returns the path to the xray binary, or empty string.
func FindBinary() string {
	for _, p := range []string{"/usr/local/bin/xray", "/usr/bin/xray", "/opt/xray/xray"} {
		if _, err := os.Stat(p); err == nil {
			return p
		}
	}
	if out, err := exec.Command("which", "xray").Output(); err == nil {
		return strings.TrimSpace(string(out))
	}
	return ""
}

// GetInstalledVersion returns the currently installed xray version string.
func GetInstalledVersion() string {
	out, err := exec.Command("xray", "version").Output()
	if err != nil {
		return "unknown"
	}
	re := regexp.MustCompile(`Xray\s+(\d+\.\d+\.\d+)`)
	m := re.FindStringSubmatch(string(out))
	if len(m) > 1 {
		return m[1]
	}
	return strings.TrimSpace(strings.Split(string(out), "\n")[0])
}

// GetStatus returns the current service status.
func (m *Manager) GetStatus() Status {
	var s Status
	s.Version = GetInstalledVersion()

	_, activeOut := runCmd("systemctl", "is-active", "xray")
	s.Running = strings.TrimSpace(activeOut) == "active"

	_, enabledOut := runCmd("systemctl", "is-enabled", "xray")
	s.Enabled = strings.TrimSpace(enabledOut) == "enabled"

	// PID and memory from systemctl show
	_, showOut := runCmd("systemctl", "show", "xray",
		"--property=MainPID,MemoryCurrent,ActiveEnterTimestamp")
	for _, line := range strings.Split(showOut, "\n") {
		kv := strings.SplitN(line, "=", 2)
		if len(kv) != 2 {
			continue
		}
		switch kv[0] {
		case "MainPID":
			fmt.Sscanf(kv[1], "%d", &s.PID)
		case "MemoryCurrent":
			fmt.Sscanf(kv[1], "%d", &s.Memory)
		case "ActiveEnterTimestamp":
			s.Uptime = parseUptime(kv[1])
		}
	}
	return s
}

func parseUptime(ts string) string {
	formats := []string{
		"Mon 2006-01-02 15:04:05 MST",
		"Mon 2006-01-02 15:04:05 UTC",
	}
	var t time.Time
	var err error
	for _, f := range formats {
		t, err = time.Parse(f, ts)
		if err == nil {
			break
		}
	}
	if err != nil {
		return ""
	}
	d := time.Since(t)
	h := int(d.Hours())
	m := int(d.Minutes()) % 60
	if h >= 24 {
		return fmt.Sprintf("%dd %dh", h/24, h%24)
	}
	return fmt.Sprintf("%dh %dm", h, m)
}

// Start starts the xray service.
func (m *Manager) Start() (bool, string) {
	ok, out := runCmd("systemctl", "start", "xray")
	if !ok {
		return false, out
	}
	time.Sleep(1500 * time.Millisecond)
	return verifyAlive()
}

// Stop stops the xray service.
func (m *Manager) Stop() (bool, string) {
	return runCmd("systemctl", "stop", "xray")
}

// Restart restarts the xray service.
func (m *Manager) Restart() (bool, string) {
	ok, out := runCmd("systemctl", "restart", "xray")
	if !ok {
		return false, out
	}
	time.Sleep(1500 * time.Millisecond)
	return verifyAlive()
}

// Reload sends SIGHUP (reloads config without restart).
func (m *Manager) Reload() (bool, string) {
	ok, out := runCmd("systemctl", "reload-or-restart", "xray")
	if !ok {
		return false, out
	}
	time.Sleep(1000 * time.Millisecond)
	return verifyAlive()
}

// Enable enables xray autostart.
func (m *Manager) Enable() (bool, string) {
	return runCmd("systemctl", "enable", "xray")
}

// Disable disables xray autostart.
func (m *Manager) Disable() (bool, string) {
	return runCmd("systemctl", "disable", "xray")
}

type ghRelease struct {
	TagName string `json:"tag_name"`
	Assets  []struct {
		Name               string `json:"name"`
		BrowserDownloadURL string `json:"browser_download_url"`
	} `json:"assets"`
}

// GetLatestVersion fetches the latest Xray-core release from GitHub (cached 5min).
func (m *Manager) GetLatestVersion() (tag, downloadURL string, err error) {
	m.mu.Lock()
	defer m.mu.Unlock()

	if time.Since(m.verCache.at) < 5*time.Minute && m.verCache.tag != "" {
		return m.verCache.tag, m.verCache.url, nil
	}

	client := &http.Client{Timeout: 15 * time.Second}
	req, _ := http.NewRequest(http.MethodGet,
		"https://api.github.com/repos/XTLS/Xray-core/releases/latest", nil)
	req.Header.Set("User-Agent", "xray-monitor/1.0")
	resp, err := client.Do(req)
	if err != nil {
		return "", "", err
	}
	defer resp.Body.Close()

	var rel ghRelease
	if err := json.NewDecoder(resp.Body).Decode(&rel); err != nil {
		return "", "", err
	}

	arch := archSuffix()
	for _, asset := range rel.Assets {
		name := strings.ToLower(asset.Name)
		if strings.Contains(name, arch) && strings.HasSuffix(name, ".zip") &&
			!strings.Contains(name, "dgst") {
			m.verCache = versionCache{tag: rel.TagName, url: asset.BrowserDownloadURL, at: time.Now()}
			return rel.TagName, asset.BrowserDownloadURL, nil
		}
	}
	return rel.TagName, "", fmt.Errorf("no matching asset for arch %s", arch)
}

func archSuffix() string {
	switch runtime.GOARCH {
	case "amd64":
		return "64"
	case "arm64":
		return "arm64-v8a"
	case "arm":
		return "arm32-v7a"
	case "386":
		return "32"
	default:
		return "64"
	}
}

// UpdateAsync downloads and installs the latest Xray binary in a goroutine.
// progress is called with (stage, message) updates; done is called when finished.
func (m *Manager) UpdateAsync(
	progress func(stage, msg string),
	done func(ok bool, msg string),
) {
	go func() {
		ok, msg := m.doUpdate(progress)
		done(ok, msg)
	}()
}

func (m *Manager) doUpdate(progress func(stage, msg string)) (bool, string) {
	tag, dlURL, err := m.GetLatestVersion()
	if err != nil {
		return false, "version check failed: " + err.Error()
	}
	progress("download", "Downloading "+tag+"...")

	tmpDir, err := os.MkdirTemp("", "xray-update-*")
	if err != nil {
		return false, "tmpdir: " + err.Error()
	}
	defer os.RemoveAll(tmpDir)

	zipPath := filepath.Join(tmpDir, "xray.zip")
	if err := downloadFile(dlURL, zipPath); err != nil {
		return false, "download failed: " + err.Error()
	}

	progress("extract", "Extracting...")
	binPath := filepath.Join(tmpDir, "xray")
	if err := extractBinary(zipPath, binPath); err != nil {
		return false, "extract failed: " + err.Error()
	}

	progress("verify", "Verifying binary...")
	out, err := exec.Command(binPath, "version").Output()
	if err != nil {
		return false, "binary verify failed: " + err.Error()
	}
	progress("verify", "Binary OK: "+strings.Split(string(out), "\n")[0])

	target := FindBinary()
	if target == "" {
		target = "/usr/local/bin/xray"
	}

	progress("install", "Installing to "+target+"...")
	// Backup
	backup := target + ".bak"
	_ = os.Rename(target, backup)

	data, err := os.ReadFile(binPath)
	if err != nil {
		_ = os.Rename(backup, target)
		return false, "read binary: " + err.Error()
	}
	if err := os.WriteFile(target, data, 0755); err != nil {
		_ = os.Rename(backup, target)
		return false, "write binary: " + err.Error()
	}

	progress("restart", "Restarting xray...")
	ok, msg := m.Restart()
	if !ok {
		return false, "restart failed: " + msg
	}
	return true, "Updated to " + tag
}

func downloadFile(url, dest string) error {
	client := &http.Client{Timeout: 120 * time.Second}
	resp, err := client.Get(url)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	f, err := os.Create(dest)
	if err != nil {
		return err
	}
	defer f.Close()
	_, err = io.Copy(f, resp.Body)
	return err
}

func extractBinary(zipPath, destPath string) error {
	r, err := zip.OpenReader(zipPath)
	if err != nil {
		return err
	}
	defer r.Close()
	for _, f := range r.File {
		if f.Name == "xray" || f.Name == "xray.exe" {
			rc, err := f.Open()
			if err != nil {
				return err
			}
			defer rc.Close()
			data, err := io.ReadAll(rc)
			if err != nil {
				return err
			}
			return os.WriteFile(destPath, data, 0755)
		}
	}
	return fmt.Errorf("xray binary not found in zip")
}

func runCmd(name string, args ...string) (bool, string) {
	out, err := exec.Command(name, args...).CombinedOutput()
	return err == nil, strings.TrimSpace(string(out))
}

func verifyAlive() (bool, string) {
	_, out := runCmd("systemctl", "is-active", "xray")
	if strings.TrimSpace(out) == "active" {
		return true, "Service is active"
	}
	// Get recent journal
	_, logs := runCmd("journalctl", "-u", "xray", "-n", "5", "--no-pager", "-o", "cat")
	return false, "Service not active. Recent logs:\n" + logs
}

// JournalLogs returns the last n lines from journalctl for xray.
func JournalLogs(n int) string {
	_, out := runCmd("journalctl", "-u", "xray",
		fmt.Sprintf("-n%d", n), "--no-pager", "--output=short")
	return out
}
