package tui

import (
	"fmt"
	"strings"

	"github.com/charmbracelet/lipgloss"

	"github.com/graynextry/xray-monitor/internal/sni"
)

// renderLogLines turns raw log lines into colorized terminal strings.
func renderLogLines(lines []string, width int) string {
	var sb strings.Builder
	for _, line := range lines {
		sb.WriteString(colorLogLine(line) + "\n")
	}
	return sb.String()
}

func colorLogLine(line string) string {
	lower := strings.ToLower(line)
	switch {
	case strings.Contains(lower, "blocked") || strings.Contains(lower, "reject"):
		return StyleErr.Render(line)
	case strings.Contains(lower, "failed") || strings.Contains(lower, "error"):
		return StyleWarn.Render(line)
	case strings.Contains(lower, "accepted"):
		// Highlight the email portion
		if idx := strings.Index(line, "email:"); idx != -1 {
			before := line[:idx]
			after := line[idx:]
			return StyleDim.Render(before) + StyleAccent.Render(after)
		}
		return StyleDim.Render(line)
	default:
		return StyleDim.Render(line)
	}
}

// renderSNIRadar renders the SNI service breakdown panel.
func renderSNIRadar(m Model, w, h int) string {
	// Aggregate sniAcc by service tag
	type svcRow struct {
		label    string
		colorKey string
		count    int
	}
	byTag := make(map[string]*svcRow)
	for _, domains := range m.sniAcc {
		for domain, count := range domains {
			c := sni.Classify(domain)
			if c == nil {
				c = &sni.Classification{Tag: "other", Label: "Other", ColorKey: "dim"}
			}
			if r, ok := byTag[c.Tag]; ok {
				r.count += count
			} else {
				byTag[c.Tag] = &svcRow{label: c.Label, colorKey: c.ColorKey, count: count}
			}
		}
	}

	rows := make([]svcRow, 0, len(byTag))
	for _, r := range byTag {
		rows = append(rows, *r)
	}
	// Sort by count desc
	for i := 0; i < len(rows); i++ {
		for j := i + 1; j < len(rows); j++ {
			if rows[j].count > rows[i].count {
				rows[i], rows[j] = rows[j], rows[i]
			}
		}
	}

	var sb strings.Builder
	sb.WriteString(StyleTitle.Render(" SNI RADAR ") + "\n")
	sb.WriteString(StyleDim.Render(Pad("Service", 20)+"Hits") + "\n")
	sb.WriteString(HRule(w-4) + "\n")

	maxRows := h - 5
	for i, r := range rows {
		if i >= maxRows {
			break
		}
		color := SNIColor(r.colorKey)
		label := r.label
		if len(label) > 18 {
			label = label[:15] + "..."
		}
		sb.WriteString(
			lipgloss.NewStyle().Foreground(color).Render(Pad(label, 20)) +
				StyleDim.Render(PadLeft(fmt.Sprintf("%d", r.count), 6)) + "\n",
		)
	}
	if len(rows) == 0 {
		sb.WriteString(StyleDim.Render("  No SNI data yet"))
	}

	return StylePanel.Width(w).Height(h).Render(sb.String())
}

// renderTopBlocked renders the top blocked targets panel.
func renderTopBlocked(m Model, w, h int) string {
	entries := m.logTail.TopBlocked(20)
	var sb strings.Builder
	sb.WriteString(StyleTitle.Render(" TOP BLOCKED ") + "\n")
	sb.WriteString(StyleDim.Render(Pad("Target", w-10)+"Count") + "\n")
	sb.WriteString(HRule(w-4) + "\n")

	maxRows := h - 5
	for i, e := range entries {
		if i >= maxRows {
			break
		}
		target := e.Target
		maxT := w - 14
		if len(target) > maxT {
			target = target[:maxT-3] + "..."
		}
		sb.WriteString(StyleErr.Render(Pad(target, maxT+2)) +
			StyleDim.Render(PadLeft(fmt.Sprintf("%d", e.Count), 6)) + "\n")
	}
	if len(entries) == 0 {
		sb.WriteString(StyleDim.Render("  No blocked requests"))
	}

	return StylePanel.Width(w).Height(h).Render(sb.String())
}

