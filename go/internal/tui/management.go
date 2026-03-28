package tui

import (
	"fmt"
	"strings"

	"github.com/charmbracelet/lipgloss"
	tea "github.com/charmbracelet/bubbletea"

	"github.com/graynextry/xray-monitor/internal/xray"
)

func renderManagement(m Model) string {
	w := m.width
	h := m.height - 3
	leftW := w * 2 / 3
	rightW := w - leftW - 3

	left := renderXrayControl(m, leftW, h)
	right := renderMgmtHelp(m, rightW, h)
	return lipgloss.JoinHorizontal(lipgloss.Top, left, "  ", right)
}

func renderXrayControl(m Model, w, h int) string {
	st := m.lastXrayStat
	ver := m.lastXrayStat.Version
	if ver == "" {
		ver = "unknown"
	}

	var sb strings.Builder
	sb.WriteString(StyleTitle.Render(" XRAY MANAGEMENT ") + "\n")

	// Service status
	statusDot := StyleErr.Render("●")
	statusWord := StyleErr.Render("stopped")
	if st.Running {
		statusDot = StyleOnline.Render("●")
		statusWord = StyleOK.Render("running")
	}
	bootBadge := StyleDim.Render("disabled")
	if st.Enabled {
		bootBadge = StyleOK.Render("enabled")
	}

	sb.WriteString("\n")
	sb.WriteString("  " + StyleDim.Render("STATUS   ") + statusDot + " " + statusWord + "\n")
	sb.WriteString("  " + StyleDim.Render("VERSION  ") + StyleText.Render(ver) + "\n")
	sb.WriteString("  " + StyleDim.Render("AUTOBOOT ") + bootBadge + "\n")
	if st.Memory > 0 {
		sb.WriteString("  " + StyleDim.Render("MEMORY   ") + StyleTotal.Render(FmtBytes(st.Memory)) + "\n")
	}
	if st.PID > 0 {
		sb.WriteString("  " + StyleDim.Render("PID      ") + StyleDim.Render(fmt.Sprintf("%d", st.PID)) + "\n")
	}
	if st.Uptime != "" {
		sb.WriteString("  " + StyleDim.Render("UPTIME   ") + StyleDim.Render(st.Uptime) + "\n")
	}

	sb.WriteString("\n" + HRule(w-4) + "\n")

	// Update section
	sb.WriteString(StyleTitle.Render(" UPDATE ") + "\n")
	if m.updating {
		sb.WriteString(StyleWarn.Render("  Updating in progress...\n"))
	}
	for _, line := range m.updateLines {
		sb.WriteString("  " + line + "\n")
	}
	if !m.updating && len(m.updateLines) == 0 {
		sb.WriteString(StyleDim.Render("  Press U to check for updates\n"))
	}

	sb.WriteString("\n" + HRule(w-4) + "\n")

	// Journal logs section
	sb.WriteString(StyleTitle.Render(" JOURNAL ") + "\n")
	journal := xray.JournalLogs(15)
	for _, line := range strings.Split(journal, "\n") {
		if line == "" {
			continue
		}
		sb.WriteString(StyleDim.Render("  "+line) + "\n")
	}

	return StylePanel.Width(w).Height(h).Render(sb.String())
}

func renderMgmtHelp(m Model, w, h int) string {
	var sb strings.Builder
	sb.WriteString(StyleTitle.Render(" CONTROLS ") + "\n")
	sb.WriteString("\n")

	rows := [][]string{
		{"r", "Restart xray"},
		{"S", "Start xray"},
		{"X", "Stop xray"},
		{"e", "Enable autostart"},
		{"d", "Disable autostart"},
		{"U", "Check & install update"},
		{"6 / Tab", "Return to this tab"},
	}

	for _, r := range rows {
		key := lipgloss.NewStyle().
			Foreground(lipgloss.Color(cBase)).
			Background(lipgloss.Color(cMauve)).
			Padding(0, 1).
			Render(r[0])
		sb.WriteString(key + "  " + StyleText.Render(r[1]) + "\n\n")
	}

	sb.WriteString(HRule(w-4) + "\n")
	sb.WriteString(StyleTitle.Render(" INFO ") + "\n")
	sb.WriteString(StyleDim.Render("  Geo: "+m.geo.Backend()) + "\n")
	sb.WriteString(StyleDim.Render("  Server: "+m.opts.Server) + "\n")
	sb.WriteString(StyleDim.Render("  Config: "+m.opts.ConfigPath) + "\n")

	return StylePanel.Width(w).Height(h).Render(sb.String())
}

// handleManagementKey processes management-specific key presses.
// Called from app.go's handleKey when activeTab == tabManagement.
func handleManagementKey(m Model, msg tea.KeyMsg) (Model, tea.Cmd) {
	switch msg.String() {
	case "r", "R":
		return m, func() tea.Msg {
			ok, out := m.xrayMgr.Restart()
			if ok {
				return statusFlashMsg("Xray restarted")
			}
			return statusFlashMsg("Restart failed: " + out)
		}
	case "S":
		return m, func() tea.Msg {
			ok, out := m.xrayMgr.Start()
			if ok {
				return xrayStatusMsg(m.xrayMgr.GetStatus())
			}
			return statusFlashMsg("Start failed: " + out)
		}
	case "X":
		return m, func() tea.Msg {
			ok, out := m.xrayMgr.Stop()
			if ok {
				return xrayStatusMsg(m.xrayMgr.GetStatus())
			}
			return statusFlashMsg("Stop failed: " + out)
		}
	case "e":
		return m, func() tea.Msg {
			m.xrayMgr.Enable()
			return xrayStatusMsg(m.xrayMgr.GetStatus())
		}
	case "d":
		return m, func() tea.Msg {
			m.xrayMgr.Disable()
			return xrayStatusMsg(m.xrayMgr.GetStatus())
		}
	case "U":
		if !m.updating {
			m.updating = true
			m.updateLines = nil
			return m, func() tea.Msg {
				// Check version first
				tag, _, err := m.xrayMgr.GetLatestVersion()
				if err != nil {
					return updateDoneMsg{ok: false, text: err.Error()}
				}
				installed := xray.GetInstalledVersion()
				if tag == "v"+installed || tag == installed {
					return updateDoneMsg{ok: true, text: "Already up to date (" + installed + ")"}
				}
				return updateProgressMsg{stage: "init", text: "Starting update to " + tag}
			}
		}
	}
	return m, nil
}
