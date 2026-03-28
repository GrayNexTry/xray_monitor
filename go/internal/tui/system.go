package tui

import (
	"fmt"
	"strings"

	"github.com/charmbracelet/lipgloss"
)

func renderSystem(m Model) string {
	w := m.width
	contentH := m.height - 3
	if contentH < 6 {
		return ""
	}

	// StylePanel.Height(h) = inner height; border adds 2 per row.
	// 2 rows × 2 = 4 overhead → subtract so rendered total == contentH.
	available := contentH - 4
	if available < 4 {
		available = 4
	}
	topH := available / 2
	botH := available - topH

	// Top row: two equal-width panels separated by 2 spaces
	halfW := (w - 2) / 2
	rightW := w - 2 - halfW // absorbs rounding when w is odd

	topLeft := renderCPURam(m, halfW, topH)
	topRight := renderNetDisk(m, rightW, topH)
	topRow := lipgloss.JoinHorizontal(lipgloss.Top, topLeft, "  ", topRight)

	// Bottom row: processes span full width
	bottomRow := renderProcs(m, w-2, botH)

	return lipgloss.JoinVertical(lipgloss.Left, topRow, bottomRow)
}

func renderCPURam(m Model, w, h int) string {
	sys := m.lastSys
	var sb strings.Builder

	sb.WriteString(StyleTitle.Render(" CPU & MEMORY ") + "\n")

	// Overall CPU
	cpuColor := PctColor(sys.CPUPct)
	bar := lipgloss.NewStyle().Foreground(cpuColor).Render(PctBar(sys.CPUPct, 20))
	sb.WriteString(StyleDim.Render("CPU  ") + "[" + bar + "] " +
		lipgloss.NewStyle().Foreground(cpuColor).Render(fmt.Sprintf("%.1f%%", sys.CPUPct)) + "\n")

	// RAM
	ramPct := 0.0
	if sys.RAMTotal > 0 {
		ramPct = float64(sys.RAMUsed) / float64(sys.RAMTotal) * 100
	}
	ramColor := PctColor(ramPct)
	ramBar := lipgloss.NewStyle().Foreground(ramColor).Render(PctBar(ramPct, 20))
	sb.WriteString(StyleDim.Render("RAM  ") + "[" + ramBar + "] " +
		lipgloss.NewStyle().Foreground(ramColor).Render(fmt.Sprintf("%.1f%%", ramPct)) +
		" " + StyleDim.Render(FmtBytes(int64(sys.RAMUsed))+"/"+FmtBytes(int64(sys.RAMTotal))) + "\n")

	// Swap
	swapPct := 0.0
	if sys.SwapTotal > 0 {
		swapPct = float64(sys.SwapUsed) / float64(sys.SwapTotal) * 100
	}
	swapColor := PctColor(swapPct)
	swapBar := lipgloss.NewStyle().Foreground(swapColor).Render(PctBar(swapPct, 20))
	sb.WriteString(StyleDim.Render("SWAP ") + "[" + swapBar + "] " +
		lipgloss.NewStyle().Foreground(swapColor).Render(fmt.Sprintf("%.1f%%", swapPct)) + "\n")

	sb.WriteString(HRule(w-4) + "\n")

	// Xray process info
	if sys.XrayPID > 0 {
		sb.WriteString(StyleDim.Render("XRAY ") +
			StyleOK.Render("●") + " PID " + fmt.Sprintf("%d", sys.XrayPID) +
			"  CPU " + fmt.Sprintf("%.1f%%", sys.XrayCPU) +
			"  MEM " + FmtBytes(int64(sys.XrayMem)) + "\n")
	} else {
		sb.WriteString(StyleDim.Render("XRAY ") + StyleErr.Render("● not found") + "\n")
	}

	// TCP connections
	sb.WriteString(StyleDim.Render(fmt.Sprintf("TCP  established: %d  listening: %d  procs: %d",
		sys.TCPEst, sys.TCPListen, sys.NumProcs)) + "\n")

	return StylePanel.Width(w).Height(h).Render(sb.String())
}

func renderNetDisk(m Model, w, h int) string {
	sys := m.lastSys
	var sb strings.Builder

	sb.WriteString(StyleTitle.Render(" NETWORK & DISK ") + "\n")

	// Network IO
	sb.WriteString(StyleDim.Render("NET  ") +
		StyleUp.Render("↑ "+FmtSpeed(sys.TxPerSec)) + "   " +
		StyleDn.Render("↓ "+FmtSpeed(sys.RxPerSec)) + "\n")
	sb.WriteString(StyleDim.Render("     total ↑ "+FmtBytes(int64(sys.TxTotal))) + "  " +
		StyleDim.Render("↓ "+FmtBytes(int64(sys.RxTotal))) + "\n")

	sb.WriteString(HRule(w-4) + "\n")

	// Disk
	diskColor := PctColor(sys.DiskPct)
	diskBar := lipgloss.NewStyle().Foreground(diskColor).Render(PctBar(sys.DiskPct, 20))
	sb.WriteString(StyleDim.Render("DISK ") + "[" + diskBar + "] " +
		lipgloss.NewStyle().Foreground(diskColor).Render(fmt.Sprintf("%.1f%%", sys.DiskPct)) + "\n")
	sb.WriteString(StyleDim.Render("     "+FmtBytes(int64(sys.DiskUsed))+" / "+FmtBytes(int64(sys.DiskTotal))) + "\n")

	sb.WriteString(HRule(w-4) + "\n")

	// Xray gRPC stats
	if snap := m.lastSnap; snap != nil {
		ss := snap.SysStats
		sb.WriteString(StyleTitle.Render(" XRAY RUNTIME ") + "\n")
		sb.WriteString(StyleDim.Render(fmt.Sprintf(
			"goroutines: %d  GC runs: %d\nalloc: %s  sys: %s\nuptime: %s",
			ss.Goroutines, ss.GCRuns,
			FmtBytes(int64(ss.Alloc)), FmtBytes(int64(ss.Sys)),
			FmtUptime(ss.Uptime),
		)) + "\n")
	}

	return StylePanel.Width(w).Height(h).Render(sb.String())
}

func renderProcs(m Model, w, h int) string {
	sys := m.lastSys
	var sb strings.Builder

	sb.WriteString(StyleTitle.Render(" TOP PROCESSES ") + "\n")
	sb.WriteString(StyleDim.Render(Pad("PID", 8)+Pad("NAME", 20)+Pad("MEM", 10)+"CPU%") + "\n")
	sb.WriteString(HRule(w-4) + "\n")

	maxRows := h - 5
	for i, p := range sys.TopProcs {
		if i >= maxRows {
			break
		}
		name := p.Name
		if len(name) > 18 {
			name = name[:15] + "..."
		}
		sb.WriteString(
			StyleDim.Render(PadLeft(fmt.Sprintf("%d", p.PID), 7)+" ") +
				Pad(name, 20) +
				StyleTotal.Render(PadLeft(FmtBytes(int64(p.Mem)), 9)+" ") +
				StyleDim.Render(fmt.Sprintf("%.1f%%", p.CPU)) + "\n",
		)
	}

	return StylePanel.Width(w).Height(h).Render(sb.String())
}
