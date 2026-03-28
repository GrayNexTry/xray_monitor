// Package logtail monitors an Xray access.log file in real time.
package logtail

import (
	"os"
	"regexp"
	"sort"
	"strings"
	"sync"
	"time"
)

var (
	reAccepted = regexp.MustCompile(`accepted\s+(?:tcp|udp):([^:\s\[]+):(\d+)`)
	reEmail    = regexp.MustCompile(`email:\s*(\S+)`)
	reSNI      = regexp.MustCompile(`(?:tls:|sni:)([a-zA-Z][a-zA-Z0-9._\-]{2,}\.[a-zA-Z]{2,})`)
	reSrcIPv4  = regexp.MustCompile(`(\d{1,3}(?:\.\d{1,3}){3}):\d{2,5}\s+accepted`)
	reSrcIPv6  = regexp.MustCompile(`\[([0-9a-fA-F:]{3,})\]:\d{2,5}\s+accepted`)
	reBlocked  = regexp.MustCompile(`(?i)(blocked|reject|reroute)`)
	reTS       = regexp.MustCompile(`^\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}`)
)

// SNIEntry tracks domain visits for a single IP.
type SNIEntry struct {
	Domain  string
	Tag     string
	Count   int
	LastTS  float64
}

// LogTail tails an Xray access.log file.
type LogTail struct {
	path    string
	maxLines int

	mu         sync.Mutex
	lastPos    int64
	lastSize   int64

	blockTotal   int
	blockSession int
	blockWindow  []float64 // unix timestamps of block events (ring-like, max 600)

	topBlocked map[string]int // target → count

	// ClientIPs: email → ip → last_seen unix timestamp
	ClientIPs map[string]map[string]float64

	// sniFlush: ip → domain → SNIEntry, swapped out by FlushNewSNI
	sniFlush map[string]map[string]*SNIEntry
}

// New creates a LogTail for the given path, returning last maxLines lines from Read().
func New(path string, maxLines int) *LogTail {
	return &LogTail{
		path:       path,
		maxLines:   maxLines,
		topBlocked: make(map[string]int),
		ClientIPs:  make(map[string]map[string]float64),
		sniFlush:   make(map[string]map[string]*SNIEntry),
	}
}

// Read returns the last maxLines lines from the log file.
func (l *LogTail) Read() []string {
	f, err := os.Open(l.path)
	if err != nil {
		return nil
	}
	defer f.Close()

	info, err := f.Stat()
	if err != nil {
		return nil
	}
	size := info.Size()
	const chunk = 65536
	offset := size - chunk
	if offset < 0 {
		offset = 0
	}
	if _, err := f.Seek(offset, 0); err != nil {
		return nil
	}

	buf := make([]byte, size-offset)
	n, _ := f.Read(buf)
	text := string(buf[:n])

	lines := strings.Split(text, "\n")
	if len(lines) > l.maxLines {
		lines = lines[len(lines)-l.maxLines:]
	}
	return lines
}

// UpdateBlockStats does an incremental scan for block events, connected IPs, and SNI data.
func (l *LogTail) UpdateBlockStats() {
	f, err := os.Open(l.path)
	if err != nil {
		return
	}
	defer f.Close()

	info, err := f.Stat()
	if err != nil {
		return
	}
	size := info.Size()

	l.mu.Lock()
	defer l.mu.Unlock()

	// First run: scan last 2MB
	if l.lastPos == 0 && size > 0 {
		const initial = 2 * 1024 * 1024
		l.lastPos = size - initial
		if l.lastPos < 0 {
			l.lastPos = 0
		}
	}

	if size < l.lastSize {
		// File was rotated
		l.lastPos = 0
	}
	l.lastSize = size

	if l.lastPos >= size {
		return
	}

	if _, err := f.Seek(l.lastPos, 0); err != nil {
		return
	}

	buf := make([]byte, size-l.lastPos)
	n, _ := f.Read(buf)
	l.lastPos += int64(n)

	now := float64(time.Now().Unix())
	lines := strings.Split(string(buf[:n]), "\n")

	for _, line := range lines {
		if line == "" {
			continue
		}
		if reBlocked.MatchString(line) {
			l.blockTotal++
			l.blockSession++
			l.blockWindow = append(l.blockWindow, now)
			if len(l.blockWindow) > 600 {
				l.blockWindow = l.blockWindow[len(l.blockWindow)-600:]
			}
			// Track blocked target
			if m := reAccepted.FindStringSubmatch(line); len(m) > 1 {
				target := m[1] + ":" + m[2]
				l.topBlocked[target]++
				if len(l.topBlocked) > 500 {
					// Evict one entry
					for k := range l.topBlocked {
						delete(l.topBlocked, k)
						break
					}
				}
			}
			continue
		}

		if !strings.Contains(line, "accepted") {
			continue
		}

		email := ""
		if m := reEmail.FindStringSubmatch(line); len(m) > 1 {
			email = m[1]
		}
		srcIP := ""
		if m := reSrcIPv4.FindStringSubmatch(line); len(m) > 1 {
			srcIP = m[1]
		} else if m := reSrcIPv6.FindStringSubmatch(line); len(m) > 1 {
			srcIP = m[1]
		}
		domain := ""
		if m := reSNI.FindStringSubmatch(line); len(m) > 1 {
			domain = strings.ToLower(m[1])
		}

		if email != "" && srcIP != "" {
			if l.ClientIPs[email] == nil {
				l.ClientIPs[email] = make(map[string]float64)
			}
			l.ClientIPs[email][srcIP] = now
		}

		if srcIP != "" && domain != "" {
			if l.sniFlush[srcIP] == nil {
				l.sniFlush[srcIP] = make(map[string]*SNIEntry)
			}
			if e, ok := l.sniFlush[srcIP][domain]; ok {
				e.Count++
				e.LastTS = now
			} else {
				l.sniFlush[srcIP][domain] = &SNIEntry{
					Domain: domain, Count: 1, LastTS: now,
				}
			}
		}
	}

	// Prune stale ClientIPs (older than 24h)
	cutoff := now - 86400
	for email, ips := range l.ClientIPs {
		for ip, ts := range ips {
			if ts < cutoff {
				delete(ips, ip)
			}
		}
		if len(ips) == 0 {
			delete(l.ClientIPs, email)
		}
	}
}

// GetClientIPs returns a snapshot of email → ip → timestamp.
func (l *LogTail) GetClientIPs() map[string]map[string]float64 {
	l.mu.Lock()
	defer l.mu.Unlock()
	out := make(map[string]map[string]float64, len(l.ClientIPs))
	for email, ips := range l.ClientIPs {
		m := make(map[string]float64, len(ips))
		for ip, ts := range ips {
			m[ip] = ts
		}
		out[email] = m
	}
	return out
}

// FlushNewSNI swaps out the sniFlush map and returns it.
func (l *LogTail) FlushNewSNI() map[string]map[string]*SNIEntry {
	l.mu.Lock()
	defer l.mu.Unlock()
	old := l.sniFlush
	l.sniFlush = make(map[string]map[string]*SNIEntry)
	return old
}

// BlockPerMin returns the approximate block rate over the last 5 minutes.
func (l *LogTail) BlockPerMin() float64 {
	l.mu.Lock()
	defer l.mu.Unlock()
	cutoff := float64(time.Now().Unix()) - 300
	n := 0
	for _, ts := range l.blockWindow {
		if ts >= cutoff {
			n++
		}
	}
	return float64(n) / 5.0
}

// BlockStats returns (total, session, perMin).
func (l *LogTail) BlockStats() (int, int, float64) {
	l.mu.Lock()
	defer l.mu.Unlock()
	cutoff := float64(time.Now().Unix()) - 300
	n := 0
	for _, ts := range l.blockWindow {
		if ts >= cutoff {
			n++
		}
	}
	return l.blockTotal, l.blockSession, float64(n) / 5.0
}

// TopBlockedEntry is one row from the top-blocked table.
type TopBlockedEntry struct {
	Target string
	Count  int
}

// TopBlocked returns the top n blocked targets by count.
func (l *LogTail) TopBlocked(n int) []TopBlockedEntry {
	l.mu.Lock()
	defer l.mu.Unlock()
	entries := make([]TopBlockedEntry, 0, len(l.topBlocked))
	for t, c := range l.topBlocked {
		entries = append(entries, TopBlockedEntry{t, c})
	}
	sort.Slice(entries, func(i, j int) bool {
		return entries[i].Count > entries[j].Count
	})
	if len(entries) > n {
		return entries[:n]
	}
	return entries
}
