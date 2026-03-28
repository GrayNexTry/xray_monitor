package tui

import (
	"fmt"
	"math"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/charmbracelet/lipgloss"
)

// ── Catppuccin Mocha palette ──────────────────────────────────────────────────
const (
	cBase     = "#1e1e2e"
	cMantle   = "#181825"
	cSurface0 = "#313244"
	cSurface1 = "#45475a"
	cOverlay0 = "#6c7086"
	cText     = "#cdd6f4"
	cSubtext0 = "#a6adc8"
	cSubtext1 = "#bac2de"
	cLavender = "#b4befe"
	cBlue     = "#89b4fa"
	cSapphire = "#74c7ec"
	cSky      = "#89dceb"
	cTeal     = "#94e2d5"
	cGreen    = "#a6e3a1"
	cYellow   = "#f9e2af"
	cPeach    = "#fab387"
	cMaroon   = "#eba0ac"
	cRed      = "#f38ba8"
	cMauve    = "#cba6f7"
	cPink     = "#f5c2e7"
)

// Color map for sni.Classification.ColorKey
var colorMap = map[string]lipgloss.Color{
	"red":     lipgloss.Color(cRed),
	"green":   lipgloss.Color(cGreen),
	"blue":    lipgloss.Color(cBlue),
	"mauve":   lipgloss.Color(cMauve),
	"yellow":  lipgloss.Color(cYellow),
	"peach":   lipgloss.Color(cPeach),
	"sky":     lipgloss.Color(cSky),
	"teal":    lipgloss.Color(cTeal),
	"warn":    lipgloss.Color(cYellow),
	"dim":     lipgloss.Color(cOverlay0),
	"subtext": lipgloss.Color(cSubtext0),
}

// SNIColor returns the lipgloss color for a color key, defaulting to text.
func SNIColor(key string) lipgloss.Color {
	if c, ok := colorMap[key]; ok {
		return c
	}
	return lipgloss.Color(cText)
}

// ── Styles ────────────────────────────────────────────────────────────────────
var (
	StyleText     = lipgloss.NewStyle().Foreground(lipgloss.Color(cText))
	StyleDim      = lipgloss.NewStyle().Foreground(lipgloss.Color(cOverlay0))
	StyleSubtext  = lipgloss.NewStyle().Foreground(lipgloss.Color(cSubtext0))
	StyleUp       = lipgloss.NewStyle().Foreground(lipgloss.Color(cGreen))
	StyleDn       = lipgloss.NewStyle().Foreground(lipgloss.Color(cSky))
	StyleTotal    = lipgloss.NewStyle().Foreground(lipgloss.Color(cYellow))
	StyleWarn     = lipgloss.NewStyle().Foreground(lipgloss.Color(cPeach))
	StyleErr      = lipgloss.NewStyle().Foreground(lipgloss.Color(cRed))
	StyleOK       = lipgloss.NewStyle().Foreground(lipgloss.Color(cGreen))
	StyleAccent   = lipgloss.NewStyle().Foreground(lipgloss.Color(cMauve)).Bold(true)
	StyleBlue     = lipgloss.NewStyle().Foreground(lipgloss.Color(cBlue))
	StyleTeal     = lipgloss.NewStyle().Foreground(lipgloss.Color(cTeal))
	StyleSky      = lipgloss.NewStyle().Foreground(lipgloss.Color(cSky))
	StyleBold     = lipgloss.NewStyle().Bold(true)
	StyleTitle    = lipgloss.NewStyle().Foreground(lipgloss.Color(cMauve)).Bold(true)
	StyleOnline   = lipgloss.NewStyle().Foreground(lipgloss.Color(cGreen))
	StyleOffline  = lipgloss.NewStyle().Foreground(lipgloss.Color(cOverlay0))

	StylePanel = lipgloss.NewStyle().
			Border(lipgloss.RoundedBorder()).
			BorderForeground(lipgloss.Color(cSurface1)).
			Padding(0, 1)

	StyleTabActive = lipgloss.NewStyle().
			Foreground(lipgloss.Color(cBase)).
			Background(lipgloss.Color(cMauve)).
			Padding(0, 2).
			Bold(true)

	StyleTabInactive = lipgloss.NewStyle().
				Foreground(lipgloss.Color(cSubtext0)).
				Padding(0, 2)

	StyleStatusBar = lipgloss.NewStyle().
			Foreground(lipgloss.Color(cSubtext0)).
			Background(lipgloss.Color(cMantle)).
			Width(0) // width set dynamically
)

// ── Sparkline ─────────────────────────────────────────────────────────────────
const sparkChars = "▁▂▃▄▅▆▇█"

// Sparkline renders vals as a Unicode block sparkline of given width.
func Sparkline(vals []float64, width int) string {
	if width <= 0 {
		return ""
	}
	// Use last `width` values
	if len(vals) > width {
		vals = vals[len(vals)-width:]
	}
	if len(vals) == 0 {
		return strings.Repeat(" ", width)
	}

	maxV := 0.0
	for _, v := range vals {
		if v > maxV {
			maxV = v
		}
	}

	runes := []rune(sparkChars)
	var sb strings.Builder
	for _, v := range vals {
		idx := 0
		if maxV > 0 {
			idx = int(math.Round(v / maxV * float64(len(runes)-1)))
		}
		if idx < 0 {
			idx = 0
		}
		if idx >= len(runes) {
			idx = len(runes) - 1
		}
		sb.WriteRune(runes[idx])
	}
	// Pad to width
	result := sb.String()
	w := utf8.RuneCountInString(result)
	if w < width {
		result = strings.Repeat(" ", width-w) + result
	}
	return result
}

// PctBar renders a filled bar: "███░░░░░" style.
func PctBar(pct, width float64) string {
	if width <= 0 {
		return ""
	}
	filled := int(pct / 100.0 * width)
	if filled < 0 {
		filled = 0
	}
	if filled > int(width) {
		filled = int(width)
	}
	return strings.Repeat("█", filled) + strings.Repeat("░", int(width)-filled)
}

// PctColor returns a color based on percentage thresholds.
func PctColor(pct float64) lipgloss.Color {
	switch {
	case pct >= 85:
		return lipgloss.Color(cRed)
	case pct >= 60:
		return lipgloss.Color(cPeach)
	default:
		return lipgloss.Color(cGreen)
	}
}

// ── Formatters ────────────────────────────────────────────────────────────────

// FmtBytes formats a byte count as a human-readable string.
func FmtBytes(n int64) string {
	switch {
	case n >= 1<<30:
		return fmt.Sprintf("%.2f GB", float64(n)/(1<<30))
	case n >= 1<<20:
		return fmt.Sprintf("%.1f MB", float64(n)/(1<<20))
	case n >= 1<<10:
		return fmt.Sprintf("%.0f KB", float64(n)/(1<<10))
	default:
		return fmt.Sprintf("%d B", n)
	}
}

// FmtBytesF formats a float64 byte count.
func FmtBytesF(n float64) string { return FmtBytes(int64(n)) }

// FmtSpeed formats bytes/sec as a speed string.
func FmtSpeed(bps float64) string {
	switch {
	case bps >= 1<<30:
		return fmt.Sprintf("%.2f GB/s", bps/(1<<30))
	case bps >= 1<<20:
		return fmt.Sprintf("%.2f MB/s", bps/(1<<20))
	case bps >= 1<<10:
		return fmt.Sprintf("%.1f KB/s", bps/(1<<10))
	default:
		return fmt.Sprintf("%.0f B/s", bps)
	}
}

// FmtUptime formats seconds into "Xd Xh Xm".
func FmtUptime(secs uint64) string {
	d := secs / 86400
	h := (secs % 86400) / 3600
	m := (secs % 3600) / 60
	if d > 0 {
		return fmt.Sprintf("%dd %dh %dm", d, h, m)
	}
	if h > 0 {
		return fmt.Sprintf("%dh %dm", h, m)
	}
	return fmt.Sprintf("%dm", m)
}

// FmtTS formats a time.Time as HH:MM:SS.
func FmtTS(t time.Time) string {
	return t.Format("15:04:05")
}

// HRule returns a horizontal rule of the given width.
func HRule(width int) string {
	return StyleDim.Render(strings.Repeat("─", width))
}

// Pad pads or truncates a string to exactly n display columns.
func Pad(s string, n int) string {
	w := utf8.RuneCountInString(s)
	if w >= n {
		runes := []rune(s)
		return string(runes[:n])
	}
	return s + strings.Repeat(" ", n-w)
}

// PadLeft right-aligns s in a field of width n.
func PadLeft(s string, n int) string {
	w := utf8.RuneCountInString(s)
	if w >= n {
		return s
	}
	return strings.Repeat(" ", n-w) + s
}
