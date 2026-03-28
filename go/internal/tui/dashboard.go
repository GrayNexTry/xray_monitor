package tui

import (
	"fmt"
	"sort"
	"strings"

	"github.com/charmbracelet/lipgloss"

	"github.com/graynextry/xray-monitor/internal/stats"
)

func renderDashboard(m Model) string {
	contentH := m.height - 3
	leftW := m.width * 5 / 8
	rightW := m.width - leftW - 3

	left := renderOverview(m, leftW, contentH/2)
	right := renderUserTable(m, rightW, contentH)
	topRow := lipgloss.JoinHorizontal(lipgloss.Top, left, "  ", right)

	bottomLeft := renderTrafficTable(m, leftW, contentH/2-2)
	bottomRight := renderSysMini(m, rightW, contentH/2-2)
	bottomRow := lipgloss.JoinHorizontal(lipgloss.Top, bottomLeft, "  ", bottomRight)

	return lipgloss.JoinVertical(lipgloss.Left, topRow, bottomRow)
}

func renderOverview(m Model, w, h int) string {
	snap := m.lastSnap
	var totalUp, totalDn, peakUp, peakDn, sessUp, sessDn float64
	var online int
	var xrayUp uint64

	if snap != nil {
		totalUp = snap.TotalUp
		totalDn = snap.TotalDown
		peakUp = snap.PeakUp
		peakDn = snap.PeakDown
		sessUp = snap.SessUp
		sessDn = snap.SessDn
		online = len(snap.Online)
		xrayUp = snap.SysStats.Uptime
	}

	upHist := m.collector.UpHistSlice(40)
	dnHist := m.collector.DnHistSlice(40)

	blkTotal, blkSession, blkPerMin := m.logTail.BlockStats()

	var sb strings.Builder

	// Title row
	title := StyleTitle.Render(" OVERVIEW ")
	onlineStr := StyleOnline.Render(fmt.Sprintf(" %d online", online))
	uptimeStr := StyleDim.Render(" · " + FmtUptime(xrayUp))
	sb.WriteString(title + onlineStr + uptimeStr + "\n")
	sb.WriteString(HRule(w-2) + "\n")

	// Upload
	upSparkStyle := lipgloss.NewStyle().Foreground(lipgloss.Color(cGreen))
	dnSparkStyle := lipgloss.NewStyle().Foreground(lipgloss.Color(cSky))

	upSpeed := FmtSpeed(totalUp)
	dnSpeed := FmtSpeed(totalDn)
	upSpark := upSparkStyle.Render(Sparkline(upHist, 30))
	dnSpark := dnSparkStyle.Render(Sparkline(dnHist, 30))

	sb.WriteString(StyleUp.Render("↑ UP    ") + Pad(upSpeed, 12) + upSpark + "\n")
	sb.WriteString(StyleDn.Render("↓ DOWN  ") + Pad(dnSpeed, 12) + dnSpark + "\n")
	sb.WriteString(HRule(w-2) + "\n")

	// Session totals
	sb.WriteString(StyleTotal.Render("  SESS  ") +
		StyleUp.Render("↑ "+FmtBytesF(sessUp)) + "  " +
		StyleDn.Render("↓ "+FmtBytesF(sessDn)) + "\n")
	sb.WriteString(StyleDim.Render("  PEAK  ") +
		StyleUp.Render("↑ "+FmtSpeed(peakUp)) + "  " +
		StyleDn.Render("↓ "+FmtSpeed(peakDn)) + "\n")
	sb.WriteString(HRule(w-2) + "\n")

	// Block stats
	sb.WriteString(StyleWarn.Render("  BLK   ") +
		StyleDim.Render(fmt.Sprintf("total %d  session %d  %.1f/min", blkTotal, blkSession, blkPerMin)) + "\n")

	content := sb.String()
	return StylePanel.Width(w).Height(h).Render(content)
}

func renderUserTable(m Model, w, h int) string {
	snap := m.lastSnap
	if snap == nil {
		return StylePanel.Width(w).Height(h).Render(StyleDim.Render("No data"))
	}

	today := m.store.GetToday()

	type row struct {
		email  string
		speed  stats.UserSpeed
		todayU int64
		todayD int64
		online bool
	}

	rows := make([]row, 0, len(snap.Users))
	onlineSet := make(map[string]bool, len(snap.Online))
	for _, e := range snap.Online {
		onlineSet[e] = true
	}

	for email, bb := range snap.Users {
		sp := snap.USpeed[email]
		tEntry := today[email]
		rows = append(rows, row{
			email:  email,
			speed:  sp,
			todayU: bb.Up,
			todayD: bb.Dn,
			online: onlineSet[email],
		})
		_ = tEntry
	}

	// Sort
	sort.Slice(rows, func(i, j int) bool {
		switch m.sortBy {
		case "up":
			return rows[i].speed.Up > rows[j].speed.Up
		case "total":
			return (rows[i].speed.Up + rows[i].speed.Dn) > (rows[j].speed.Up + rows[j].speed.Dn)
		default: // "dn"
			return rows[i].speed.Dn > rows[j].speed.Dn
		}
	})

	header := StyleDim.Render(Pad(" User", 22) + Pad("↑ Up/s", 12) + Pad("↓ Dn/s", 12) + "Today↑  Today↓")
	var sb strings.Builder
	sb.WriteString(StyleTitle.Render(fmt.Sprintf(" USERS (%d) ", len(rows))) + "\n")
	sb.WriteString(header + "\n")
	sb.WriteString(HRule(w-2) + "\n")

	linesLeft := h - 5
	for i, r := range rows {
		if i >= linesLeft {
			sb.WriteString(StyleDim.Render(fmt.Sprintf("  … %d more", len(rows)-i)))
			break
		}
		bullet := StyleOffline.Render("○")
		if r.online {
			bullet = StyleOnline.Render("●")
		}
		email := r.email
		if len(email) > 20 {
			email = email[:17] + "..."
		}
		hist := m.collector.GetUserHist(r.email)
		spark := ""
		if hist != nil {
			s := lipgloss.NewStyle().Foreground(lipgloss.Color(cSky))
			spark = s.Render(Sparkline(hist.Dn.Slice(12), 12))
		}
		sb.WriteString(bullet + " " + Pad(email, 20) + " " +
			StyleUp.Render(Pad(FmtSpeed(r.speed.Up), 11)) + " " +
			StyleDn.Render(Pad(FmtSpeed(r.speed.Dn), 11)) + " " +
			spark + "\n")
	}

	return StylePanel.Width(w).Height(h).Render(sb.String())
}

func renderTrafficTable(m Model, w, h int) string {
	snap := m.lastSnap
	if snap == nil {
		return StylePanel.Width(w).Height(h).Render(StyleDim.Render("No data"))
	}

	var sb strings.Builder
	sb.WriteString(StyleTitle.Render(" INBOUNDS ") + "\n")
	sb.WriteString(StyleDim.Render(Pad("Tag", 20)+Pad("↑ Up", 12)+Pad("↓ Down", 12)) + "\n")

	for tag, bb := range snap.Inbounds {
		label := tag
		if len(label) > 18 {
			label = label[:15] + "..."
		}
		sb.WriteString(Pad(label, 20) +
			StyleUp.Render(Pad(FmtBytes(bb.Up), 12)) +
			StyleDn.Render(FmtBytes(bb.Dn)) + "\n")
	}

	return StylePanel.Width(w).Height(h).Render(sb.String())
}

func renderSysMini(m Model, w, h int) string {
	sys := m.lastSys
	snap := m.lastSnap

	var sb strings.Builder
	sb.WriteString(StyleTitle.Render(" SYSTEM ") + "\n")

	// CPU
	cpuBar := lipgloss.NewStyle().Foreground(PctColor(sys.CPUPct)).Render(PctBar(sys.CPUPct, 16))
	sb.WriteString(StyleDim.Render("CPU  ") + cpuBar + " " + fmt.Sprintf("%.1f%%", sys.CPUPct) + "\n")

	// RAM
	ramPct := 0.0
	if sys.RAMTotal > 0 {
		ramPct = float64(sys.RAMUsed) / float64(sys.RAMTotal) * 100
	}
	ramBar := lipgloss.NewStyle().Foreground(PctColor(ramPct)).Render(PctBar(ramPct, 16))
	sb.WriteString(StyleDim.Render("RAM  ") + ramBar + " " + FmtBytes(int64(sys.RAMUsed)) + "\n")

	// Disk
	diskBar := lipgloss.NewStyle().Foreground(PctColor(sys.DiskPct)).Render(PctBar(sys.DiskPct, 16))
	sb.WriteString(StyleDim.Render("DISK ") + diskBar + " " + fmt.Sprintf("%.1f%%", sys.DiskPct) + "\n")

	// Network
	sb.WriteString(StyleUp.Render("↑ "+FmtSpeed(sys.TxPerSec)) + "  " +
		StyleDn.Render("↓ "+FmtSpeed(sys.RxPerSec)) + "\n")

	if snap != nil {
		sb.WriteString(StyleDim.Render(fmt.Sprintf("Xray goroutines: %d  alloc: %s",
			snap.SysStats.Goroutines, FmtBytes(int64(snap.SysStats.Alloc)))) + "\n")
	}

	return StylePanel.Width(w).Height(h).Render(sb.String())
}
