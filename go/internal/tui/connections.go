package tui

import (
	"fmt"
	"strings"

	"github.com/graynextry/xray-monitor/internal/geoip"
	"github.com/graynextry/xray-monitor/internal/stats"
)

// renderEventLines renders all connection events for the viewport.
func renderEventLines(col *stats.Collector, geo *geoip.GeoIP, width int) string {
	events := col.GetEvents()
	var sb strings.Builder

	sb.WriteString(StyleTitle.Render(fmt.Sprintf(" CONNECTION EVENTS (%d) ", len(events))) + "\n")
	sb.WriteString(StyleDim.Render(
		Pad("Time", 10)+Pad("St", 3)+Pad("User", 24)+Pad("IP", 18)+Pad("Location", 20),
	) + "\n")
	sb.WriteString(strings.Repeat("─", width) + "\n")

	// Show newest last — reverse iterate
	for i := len(events) - 1; i >= 0; i-- {
		ev := events[i]
		sb.WriteString(fmtConnEvent(ev, geo) + "\n")
	}

	return sb.String()
}

func fmtConnEvent(ev stats.ConnEvent, geo *geoip.GeoIP) string {
	ts := FmtTS(ev.TS)

	var bullet, kind string
	if ev.Kind == "connect" {
		bullet = StyleOnline.Render("●")
		kind = StyleOK.Render("CON")
	} else {
		bullet = StyleOffline.Render("○")
		kind = StyleDim.Render("DIS")
	}

	email := ev.Email
	if len(email) > 22 {
		email = email[:19] + "..."
	}

	ip := ev.IP
	if ip == "" {
		ip = "—"
	}

	loc := "..."
	if geo != nil && ip != "—" {
		if l := geo.Fmt(ip); l != "" {
			loc = l
		}
	}

	return StyleDim.Render(Pad(ts, 10)) + bullet + " " + kind + " " +
		StyleText.Render(Pad(email, 24)) +
		StyleDim.Render(Pad(ip, 18)) +
		StyleBlue.Render(Pad(loc, 20))
}
