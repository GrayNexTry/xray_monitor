package tui

import (
	"context"
	"fmt"
	"strings"
	"time"

	"github.com/charmbracelet/bubbles/viewport"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"

	"github.com/graynextry/xray-monitor/internal/config"
	"github.com/graynextry/xray-monitor/internal/geoip"
	"github.com/graynextry/xray-monitor/internal/logtail"
	"github.com/graynextry/xray-monitor/internal/stats"
	"github.com/graynextry/xray-monitor/internal/storage"
	"github.com/graynextry/xray-monitor/internal/sysinfo"
	"github.com/graynextry/xray-monitor/internal/xray"
	"github.com/graynextry/xray-monitor/internal/xrpc"
)

// Tab indices
const (
	tabDashboard = iota
	tabKeys
	tabSystem
	tabLogs
	tabConnections
	tabManagement
	tabCount
)

var tabNames = [tabCount]string{
	"[1] Dashboard",
	"[2] Keys",
	"[3] System",
	"[4] Logs",
	"[5] Events",
	"[6] Manage",
}

// ── Message types ─────────────────────────────────────────────────────────────

type tickMsg time.Time

type statsMsg struct {
	snap *stats.Snapshot
	err  error
}

type logMsg struct {
	lines     []string
	clientIPs map[string]map[string]float64
	sniFlush  map[string]map[string]*logtail.SNIEntry
}

type sysinfoMsg sysinfo.SysData

type xrayStatusMsg xray.Status

type updateProgressMsg struct{ stage, text string }
type updateDoneMsg struct{ ok bool; text string }

type statusFlashMsg string

// ── Options ───────────────────────────────────────────────────────────────────

// Options contains all runtime configuration.
type Options struct {
	Server     string
	Interval   time.Duration
	LogPath    string
	ConfigPath string
	DataPath   string
	ServerIP   string
}

// ── Model ─────────────────────────────────────────────────────────────────────

// Model is the main bubbletea model for the TUI.
type Model struct {
	opts Options

	// Layout
	width  int
	height int

	// Navigation
	activeTab int
	paused    bool
	sortBy    string // "dn" | "up" | "total"

	// Data sources
	grpcClient *xrpc.Client
	collector  *stats.Collector
	logTail    *logtail.LogTail
	geo        *geoip.GeoIP
	cfg        *config.XrayConfig
	sysCol     *sysinfo.Collector
	store      *storage.Storage
	xrayMgr    *xray.Manager

	// Latest data
	lastSnap      *stats.Snapshot
	lastSys       sysinfo.SysData
	lastLog       []string
	lastClientIPs map[string]map[string]float64
	lastXrayStat  xray.Status
	clientURLs    []config.ClientURL
	selectedURL   int

	// SNI accumulator: ip → domain → count
	sniAcc map[string]map[string]int

	// Viewports (Logs, Events)
	vpLog  viewport.Model
	vpConn viewport.Model

	// Update progress
	updateLines []string
	updating    bool

	// Status bar flash message
	statusMsg    string
	statusExpiry time.Time

	// QR
	showQR bool
	qrURL  string
}

// NewModel constructs the TUI model and wires up all data sources.
func NewModel(opts Options) (Model, error) {
	client := xrpc.New(opts.Server)
	col := stats.NewCollector(client)
	lt := logtail.New(opts.LogPath, 300)
	geo := geoip.New()
	cfg := config.New(opts.ConfigPath)
	sys := sysinfo.New()

	store, err := storage.New(opts.DataPath)
	if err != nil {
		return Model{}, fmt.Errorf("storage: %w", err)
	}

	mgr := xray.New()
	urls := cfg.BuildClientURLs(opts.ServerIP)

	m := Model{
		opts:       opts,
		activeTab:  tabDashboard,
		sortBy:     "dn",
		grpcClient: client,
		collector:  col,
		logTail:    lt,
		geo:        geo,
		cfg:        cfg,
		sysCol:     sys,
		store:      store,
		xrayMgr:    mgr,
		clientURLs: urls,
		sniAcc:     make(map[string]map[string]int),
		vpLog:      viewport.New(80, 20),
		vpConn:     viewport.New(80, 20),
	}
	return m, nil
}

// ── bubbletea interface ───────────────────────────────────────────────────────

func (m Model) Init() tea.Cmd {
	return tea.Batch(
		tickCmd(m.opts.Interval),
		fetchXrayStatusCmd(m.xrayMgr),
		collectSysInfoCmd(m.sysCol),
	)
}

func (m Model) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	var cmds []tea.Cmd

	switch msg := msg.(type) {

	case tea.WindowSizeMsg:
		m.width = msg.Width
		m.height = msg.Height
		contentH := m.height - 3 // header + status bar
		m.vpLog.Width = m.width - 4
		m.vpLog.Height = contentH
		m.vpConn.Width = m.width - 4
		m.vpConn.Height = contentH

	case tea.KeyMsg:
		return m.handleKey(msg)

	case tickMsg:
		if !m.paused {
			logIPs := m.lastClientIPs
			if logIPs == nil {
				logIPs = make(map[string]map[string]float64)
			}
			cmds = append(cmds,
				fetchStatsCmd(m.collector, logIPs),
				fetchLogCmd(m.logTail),
				collectSysInfoCmd(m.sysCol),
				tickCmd(m.opts.Interval),
			)
		} else {
			cmds = append(cmds, tickCmd(m.opts.Interval))
		}

	case statsMsg:
		if msg.err == nil && msg.snap != nil {
			m.lastSnap = msg.snap
			if m.store != nil && msg.snap.Users != nil {
				m.store.Update(msg.snap.Users)
			}
		}

	case logMsg:
		m.lastLog = msg.lines
		m.lastClientIPs = msg.clientIPs
		// Merge SNI data
		for ip, domains := range msg.sniFlush {
			if m.sniAcc[ip] == nil {
				m.sniAcc[ip] = make(map[string]int)
			}
			for domain, entry := range domains {
				m.sniAcc[ip][domain] += entry.Count
			}
		}
		// Update log viewport content
		if m.activeTab == tabLogs {
			m.vpLog.SetContent(renderLogLines(m.lastLog, m.width-4))
		}

	case sysinfoMsg:
		m.lastSys = sysinfo.SysData(msg)

	case xrayStatusMsg:
		m.lastXrayStat = xray.Status(msg)

	case updateProgressMsg:
		m.updateLines = append(m.updateLines, "["+msg.stage+"] "+msg.text)
		if len(m.updateLines) > 20 {
			m.updateLines = m.updateLines[len(m.updateLines)-20:]
		}

	case updateDoneMsg:
		m.updating = false
		if msg.ok {
			m.updateLines = append(m.updateLines, StyleOK.Render("✓ "+msg.text))
		} else {
			m.updateLines = append(m.updateLines, StyleErr.Render("✗ "+msg.text))
		}
		cmds = append(cmds, fetchXrayStatusCmd(m.xrayMgr))

	case statusFlashMsg:
		m.statusMsg = string(msg)
		m.statusExpiry = time.Now().Add(3 * time.Second)
	}

	return m, tea.Batch(cmds...)
}

func (m Model) handleKey(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
	// QR overlay
	if m.showQR {
		m.showQR = false
		return m, nil
	}

	key := msg.String()

	// Global navigation
	switch key {
	case "q", "ctrl+c":
		if m.store != nil {
			_ = m.store.Flush()
		}
		return m, tea.Quit
	case "1":
		m.activeTab = tabDashboard
	case "2":
		m.activeTab = tabKeys
		m.clientURLs = m.cfg.BuildClientURLs(m.opts.ServerIP)
	case "3":
		m.activeTab = tabSystem
	case "4":
		m.activeTab = tabLogs
		m.vpLog.SetContent(renderLogLines(m.lastLog, m.width-4))
	case "5":
		m.activeTab = tabConnections
		m.vpConn.SetContent(renderEventLines(m.collector, m.geo, m.width-4))
	case "6":
		m.activeTab = tabManagement
	case "tab":
		m.activeTab = (m.activeTab + 1) % tabCount
	case "shift+tab":
		m.activeTab = (m.activeTab - 1 + tabCount) % tabCount
	case "p":
		m.paused = !m.paused
	case "s":
		// Cycle sort order
		switch m.sortBy {
		case "dn":
			m.sortBy = "up"
		case "up":
			m.sortBy = "total"
		default:
			m.sortBy = "dn"
		}
	case "Q":
		// Show QR for selected URL
		if len(m.clientURLs) > 0 && m.selectedURL < len(m.clientURLs) {
			m.showQR = true
			m.qrURL = m.clientURLs[m.selectedURL].URL
		}
	case "r", "R":
		if m.activeTab == tabManagement {
			go func() {
				m.xrayMgr.Restart()
			}()
		}
	}

	// Per-tab handling
	var cmd tea.Cmd
	switch m.activeTab {
	case tabLogs:
		m.vpLog, cmd = m.vpLog.Update(msg)
	case tabConnections:
		m.vpConn.SetContent(renderEventLines(m.collector, m.geo, m.width-4))
		m.vpConn, cmd = m.vpConn.Update(msg)
	case tabManagement:
		m, cmd = handleManagementKey(m, msg)
	}
	return m, cmd
}

func (m Model) View() string {
	if m.width == 0 {
		return "Loading..."
	}

	if m.showQR {
		return m.renderQROverlay()
	}

	header := m.renderHeader()
	content := m.renderContent()
	status := m.renderStatusBar()

	return lipgloss.JoinVertical(lipgloss.Left, header, content, status)
}

func (m Model) renderHeader() string {
	tabs := make([]string, tabCount)
	for i, name := range tabNames {
		if i == m.activeTab {
			tabs[i] = StyleTabActive.Render(name)
		} else {
			tabs[i] = StyleTabInactive.Render(name)
		}
	}
	tabRow := strings.Join(tabs, "")

	// Right side: server + pause indicator
	right := StyleDim.Render(m.opts.Server)
	if m.paused {
		right = StyleWarn.Render("⏸ PAUSED") + "  " + right
	}

	gap := m.width - lipgloss.Width(tabRow) - lipgloss.Width(right)
	if gap < 1 {
		gap = 1
	}
	line := tabRow + strings.Repeat(" ", gap) + right
	return lipgloss.NewStyle().Background(lipgloss.Color(cMantle)).Width(m.width).Render(line)
}

func (m Model) renderContent() string {
	switch m.activeTab {
	case tabDashboard:
		return renderDashboard(m)
	case tabKeys:
		return renderKeys(m)
	case tabSystem:
		return renderSystem(m)
	case tabLogs:
		return m.vpLog.View()
	case tabConnections:
		return m.vpConn.View()
	case tabManagement:
		return renderManagement(m)
	}
	return ""
}

func (m Model) renderStatusBar() string {
	var msg string
	if m.statusMsg != "" && time.Now().Before(m.statusExpiry) {
		msg = StyleAccent.Render(" " + m.statusMsg + " ")
	}
	hints := StyleDim.Render("q:quit  Tab:switch  p:pause  s:sort  Q:qr")
	bar := hints + "  " + msg
	return lipgloss.NewStyle().
		Background(lipgloss.Color(cMantle)).
		Width(m.width).
		Render(bar)
}

func (m Model) renderQROverlay() string {
	lines := renderQRLines(m.qrURL)
	box := StylePanel.Render(
		StyleTitle.Render("  QR Code  ") + "\n" +
			strings.Join(lines, "\n") + "\n\n" +
			StyleDim.Render(m.qrURL) + "\n\n" +
			StyleDim.Render("Press any key to close"),
	)
	return lipgloss.Place(m.width, m.height, lipgloss.Center, lipgloss.Center, box)
}

// ── Command factories ─────────────────────────────────────────────────────────

func tickCmd(d time.Duration) tea.Cmd {
	return tea.Tick(d, func(t time.Time) tea.Msg { return tickMsg(t) })
}

func fetchStatsCmd(col *stats.Collector, logIPs map[string]map[string]float64) tea.Cmd {
	return func() tea.Msg {
		ctx, cancel := context.WithTimeout(context.Background(), 6*time.Second)
		defer cancel()
		snap, err := col.Fetch(ctx, logIPs)
		return statsMsg{snap: snap, err: err}
	}
}

func fetchLogCmd(lt *logtail.LogTail) tea.Cmd {
	return func() tea.Msg {
		lt.UpdateBlockStats()
		lines := lt.Read()
		ips := lt.GetClientIPs()
		sni := lt.FlushNewSNI()
		return logMsg{lines: lines, clientIPs: ips, sniFlush: sni}
	}
}

func collectSysInfoCmd(sys *sysinfo.Collector) tea.Cmd {
	return func() tea.Msg {
		sys.Collect()
		return sysinfoMsg(sys.Get())
	}
}

func fetchXrayStatusCmd(mgr *xray.Manager) tea.Cmd {
	return func() tea.Msg {
		return xrayStatusMsg(mgr.GetStatus())
	}
}
