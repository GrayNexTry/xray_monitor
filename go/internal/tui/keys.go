package tui

import (
	"fmt"
	"strings"

	"github.com/charmbracelet/lipgloss"
	qrcode "github.com/skip2/go-qrcode"
)

func renderKeys(m Model) string {
	w := m.width
	h := m.height - 3

	leftW := w * 2 / 3
	rightW := w - leftW - 3

	left := renderURLList(m, leftW, h)
	right := renderQRPanel(m, rightW, h)
	return lipgloss.JoinHorizontal(lipgloss.Top, left, "  ", right)
}

func renderURLList(m Model, w, h int) string {
	var sb strings.Builder
	sb.WriteString(StyleTitle.Render(" CLIENT URLs ") +
		StyleDim.Render(fmt.Sprintf(" (%d clients) ", len(m.clientURLs))) + "\n")
	sb.WriteString(StyleDim.Render("Q: show QR  ↑↓: select") + "\n")
	sb.WriteString(HRule(w-4) + "\n")

	maxRows := h - 5
	for i, cu := range m.clientURLs {
		if i >= maxRows {
			sb.WriteString(StyleDim.Render(fmt.Sprintf("  … %d more", len(m.clientURLs)-i)))
			break
		}
		selected := i == m.selectedURL
		prefix := "  "
		if selected {
			prefix = StyleAccent.Render("► ")
		}

		protoColor := protoStyle(cu.Protocol)
		proto := protoColor.Render(Pad(strings.ToUpper(cu.Protocol[:min(4, len(cu.Protocol))]), 5))
		sec := secBadge(cu.Security)
		email := cu.Email
		if len(email) > 22 {
			email = email[:19] + "..."
		}

		line1 := prefix + proto + " " + sec + " " + StyleText.Render(email) +
			StyleDim.Render(fmt.Sprintf("  :%d", cu.Port))
		sb.WriteString(line1 + "\n")

		url := cu.URL
		maxURLLen := w - 6
		if len(url) > maxURLLen {
			url = url[:maxURLLen-3] + "..."
		}
		sb.WriteString("    " + StyleDim.Render(url) + "\n")

		if i < len(m.clientURLs)-1 {
			sb.WriteString("\n")
		}
	}

	if len(m.clientURLs) == 0 {
		sb.WriteString(StyleWarn.Render("  No clients found in config."))
		if m.opts.ServerIP == "" {
			sb.WriteString("\n  " + StyleDim.Render("Tip: set --server-ip for correct URLs"))
		}
	}

	return StylePanel.Width(w).Height(h).Render(sb.String())
}

func renderQRPanel(m Model, w, h int) string {
	var sb strings.Builder
	sb.WriteString(StyleTitle.Render(" QR CODE ") + "\n")

	if len(m.clientURLs) == 0 {
		return StylePanel.Width(w).Height(h).Render(StyleDim.Render("No URLs"))
	}

	cu := m.clientURLs[0]
	if m.selectedURL < len(m.clientURLs) {
		cu = m.clientURLs[m.selectedURL]
	}

	lines := renderQRLines(cu.URL)
	for _, l := range lines {
		if l != "" {
			sb.WriteString(l + "\n")
		}
	}
	sb.WriteString("\n" + StyleDim.Render(cu.Email) + "\n")
	sb.WriteString(StyleDim.Render(strings.ToUpper(cu.Protocol) + "/" + cu.Network + "/" + cu.Security))

	return StylePanel.Width(w).Height(h).Render(sb.String())
}

// renderQRLines generates QR code as half-block Unicode lines.
func renderQRLines(data string) []string {
	if data == "" {
		return nil
	}
	qr, err := qrcode.New(data, qrcode.Low)
	if err != nil {
		return []string{StyleErr.Render("QR error: " + err.Error())}
	}
	bitmap := qr.Bitmap()
	return bitmapToHalfBlock(bitmap)
}

// bitmapToHalfBlock converts a QR bitmap to half-block terminal lines.
// Each cell is 2 rows in the bitmap, rendered as one terminal row using ▀▄█ characters.
func bitmapToHalfBlock(bm [][]bool) []string {
	if len(bm) == 0 {
		return nil
	}
	rows := len(bm)
	cols := len(bm[0])

	var lines []string
	for y := 0; y < rows; y += 2 {
		var sb strings.Builder
		for x := 0; x < cols; x++ {
			top := y < rows && bm[y][x]
			bottom := (y+1) < rows && bm[y+1][x]
			switch {
			case top && bottom:
				sb.WriteRune('█')
			case top:
				sb.WriteRune('▀')
			case bottom:
				sb.WriteRune('▄')
			default:
				sb.WriteRune(' ')
			}
		}
		lines = append(lines, sb.String())
	}
	return lines
}

func protoStyle(proto string) lipgloss.Style {
	switch proto {
	case "vless":
		return StyleBlue
	case "vmess":
		return StyleTeal
	case "trojan":
		return StyleWarn
	case "shadowsocks":
		return StyleSky
	default:
		return StyleDim
	}
}

func secBadge(sec string) string {
	switch sec {
	case "reality":
		return StyleAccent.Render("[REALITY]")
	case "tls":
		return StyleOK.Render("[TLS]")
	default:
		return StyleDim.Render("[NONE]")
	}
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}
