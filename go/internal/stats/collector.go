// Package stats collects and aggregates Xray traffic statistics.
package stats

import (
	"container/list"
	"context"
	"strings"
	"sync"
	"time"

	"github.com/graynextry/xray-monitor/internal/xrpc"
)

const (
	HistLen     = 45  // per-user speed history: 45 ticks ≈ 90s at 2s interval
	GlobalHist  = 90  // global speed history
	UserHistMax = 200 // max LRU users
)

// RingBuf is a fixed-size circular buffer of float64.
type RingBuf struct {
	data [HistLen]float64
	head int
	n    int
}

func (r *RingBuf) Push(v float64) {
	r.head = (r.head + 1) % HistLen
	r.data[r.head] = v
	if r.n < HistLen {
		r.n++
	}
}

// Slice returns the last min(n, r.n) values in chronological order.
func (r *RingBuf) Slice(n int) []float64 {
	if n > r.n {
		n = r.n
	}
	out := make([]float64, n)
	for i := 0; i < n; i++ {
		idx := (r.head - n + 1 + i + HistLen*100) % HistLen
		out[i] = r.data[idx]
	}
	return out
}

// UserHist tracks per-user speed history.
type UserHist struct {
	Up     RingBuf
	Dn     RingBuf
	PeakUp float64
	PeakDn float64
}

// BucketBytes holds cumulative up/down byte counters.
type BucketBytes struct {
	Up int64
	Dn int64
}

// UserSpeed is the current speed for a user.
type UserSpeed struct {
	Up float64 // bytes/sec
	Dn float64 // bytes/sec
}

// ConnEvent records a user connect or disconnect.
type ConnEvent struct {
	Kind  string // "connect" | "disconnect"
	Email string
	IP    string
	TS    time.Time
}

// IPEntry tracks per-IP cumulative traffic.
type IPEntry struct {
	Email string
	Up    float64
	Dn    float64
}

// Snapshot is the result of one Fetch call.
type Snapshot struct {
	Time       time.Time
	Users      map[string]BucketBytes // email → absolute counters
	USpeed     map[string]UserSpeed   // email → current speed
	Inbounds   map[string]BucketBytes // tag → absolute
	Outbounds  map[string]BucketBytes // tag → absolute
	TotalUp    float64                // bytes/sec
	TotalDown  float64                // bytes/sec
	SpeedUp    float64                // bytes/sec (== TotalUp)
	SpeedDown  float64                // bytes/sec
	PeakUp     float64
	PeakDown   float64
	SessUp     float64 // cumulative bytes this session
	SessDn     float64
	SysStats   xrpc.SysStats
	Online     []string // current online emails
	Error      string
}

// lruNode holds user history in an LRU list.
type lruNode struct {
	email string
	hist  *UserHist
	elem  *list.Element
}

// globalRing is a fixed-size ring buffer for global speed history.
type globalRing struct {
	data [GlobalHist]float64
	head int
	n    int
}

func (r *globalRing) push(v float64) {
	r.head = (r.head + 1) % GlobalHist
	r.data[r.head] = v
	if r.n < GlobalHist {
		r.n++
	}
}

func (r *globalRing) slice(n int) []float64 {
	if n > r.n {
		n = r.n
	}
	out := make([]float64, n)
	for i := 0; i < n; i++ {
		idx := (r.head - n + 1 + i + GlobalHist*100) % GlobalHist
		out[i] = r.data[idx]
	}
	return out
}

// Collector aggregates Xray stats.
type Collector struct {
	client *xrpc.Client

	mu    sync.Mutex
	prevAbs map[string]int64 // stat name → previous absolute value
	prevT   time.Time

	upHist  globalRing
	dnHist  globalRing

	USpeed  map[string]UserSpeed
	uHist   map[string]*lruNode
	lruList *list.List

	PeakUp  float64
	PeakDn  float64
	SessUp  float64
	SessDn  float64

	prevOnline map[string]struct{}
	Events     []ConnEvent // capped at 200

	prevLogIPs map[string]map[string]float64 // email → ip → ts
	logInit    bool

	IPTraffic map[string]*IPEntry // ip → entry (in-memory only)
}

// NewCollector creates a stats collector using the given gRPC client.
func NewCollector(client *xrpc.Client) *Collector {
	return &Collector{
		client:     client,
		prevAbs:    make(map[string]int64),
		USpeed:     make(map[string]UserSpeed),
		uHist:      make(map[string]*lruNode),
		lruList:    list.New(),
		prevOnline: make(map[string]struct{}),
		prevLogIPs: make(map[string]map[string]float64),
		IPTraffic:  make(map[string]*IPEntry),
	}
}

// Fetch polls Xray for stats and returns a Snapshot.
// logIPs is from logtail.GetClientIPs().
func (c *Collector) Fetch(ctx context.Context, logIPs map[string]map[string]float64) (*Snapshot, error) {
	// Fetch all stats (no lock — I/O)
	entries, err := c.client.QueryStats(ctx, "", false)
	if err != nil {
		return &Snapshot{Error: err.Error()}, err
	}
	sys, _ := c.client.GetSysStats(ctx)
	onlineRaw, _ := c.client.GetAllOnlineUsers(ctx)

	// Parse stat entries into buckets
	inbounds := make(map[string]BucketBytes)
	outbounds := make(map[string]BucketBytes)
	users := make(map[string]BucketBytes)

	for _, e := range entries {
		parts := strings.Split(e.Name, ">>>")
		if len(parts) != 4 {
			continue
		}
		kind, name, _, dir := parts[0], parts[1], parts[2], parts[3]
		switch kind {
		case "inbound":
			bb := inbounds[name]
			if dir == "uplink" {
				bb.Up = e.Value
			} else {
				bb.Dn = e.Value
			}
			inbounds[name] = bb
		case "outbound":
			bb := outbounds[name]
			if dir == "uplink" {
				bb.Up = e.Value
			} else {
				bb.Dn = e.Value
			}
			outbounds[name] = bb
		case "user":
			bb := users[name]
			if dir == "uplink" {
				bb.Up = e.Value
			} else {
				bb.Dn = e.Value
			}
			users[name] = bb
		}
	}

	// Parse online users
	online := make([]string, 0, len(onlineRaw))
	for _, raw := range onlineRaw {
		parts := strings.Split(raw, ">>>")
		if len(parts) >= 2 {
			online = append(online, parts[1])
		} else {
			online = append(online, raw)
		}
	}

	// Compute speeds under lock
	c.mu.Lock()
	defer c.mu.Unlock()

	now := time.Now()
	dt := now.Sub(c.prevT).Seconds()
	if dt <= 0 || c.prevT.IsZero() {
		dt = 2.0
	}
	c.prevT = now

	// Compute total user speeds
	uSpeed := make(map[string]UserSpeed, len(users))
	var totalUp, totalDn float64

	for email, bb := range users {
		upKey := "user>>>" + email + ">>>traffic>>>uplink"
		dnKey := "user>>>" + email + ">>>traffic>>>downlink"
		var su, sd float64
		if prev, ok := c.prevAbs[upKey]; ok {
			delta := bb.Up - prev
			if delta < 0 {
				delta = 0
			}
			su = float64(delta) / dt
		}
		if prev, ok := c.prevAbs[dnKey]; ok {
			delta := bb.Dn - prev
			if delta < 0 {
				delta = 0
			}
			sd = float64(delta) / dt
		}
		uSpeed[email] = UserSpeed{Up: su, Dn: sd}
		totalUp += su
		totalDn += sd
		c.updateUserHist(email, su, sd)

		// Distribute per-IP
		if ips, ok := logIPs[email]; ok && len(ips) > 0 {
			perIPUp := (bb.Up - c.prevAbs[upKey])
			perIPDn := (bb.Dn - c.prevAbs[dnKey])
			if perIPUp < 0 {
				perIPUp = 0
			}
			if perIPDn < 0 {
				perIPDn = 0
			}
			share := float64(len(ips))
			for ip := range ips {
				if _, ok := c.IPTraffic[ip]; !ok {
					c.IPTraffic[ip] = &IPEntry{Email: email}
				}
				c.IPTraffic[ip].Email = email
				c.IPTraffic[ip].Up += float64(perIPUp) / share
				c.IPTraffic[ip].Dn += float64(perIPDn) / share
			}
		}
	}

	// Update previous absolute values
	for _, e := range entries {
		c.prevAbs[e.Name] = e.Value
	}

	c.upHist.push(totalUp)
	c.dnHist.push(totalDn)
	if totalUp > c.PeakUp {
		c.PeakUp = totalUp
	}
	if totalDn > c.PeakDn {
		c.PeakDn = totalDn
	}
	c.SessUp += totalUp * dt
	c.SessDn += totalDn * dt

	c.USpeed = uSpeed

	// Track connect/disconnect events
	c.trackEvents(online, logIPs)

	return &Snapshot{
		Time:      now,
		Users:     users,
		USpeed:    uSpeed,
		Inbounds:  inbounds,
		Outbounds: outbounds,
		TotalUp:   totalUp,
		TotalDown: totalDn,
		SpeedUp:   totalUp,
		SpeedDown: totalDn,
		PeakUp:    c.PeakUp,
		PeakDown:  c.PeakDn,
		SessUp:    c.SessUp,
		SessDn:    c.SessDn,
		SysStats:  sys,
		Online:    online,
	}, nil
}

func (c *Collector) updateUserHist(email string, su, sd float64) {
	node, ok := c.uHist[email]
	if !ok {
		if c.lruList.Len() >= UserHistMax {
			back := c.lruList.Back()
			if back != nil {
				evicted := back.Value.(*lruNode)
				delete(c.uHist, evicted.email)
				c.lruList.Remove(back)
			}
		}
		h := &UserHist{}
		elem := c.lruList.PushFront(nil)
		node = &lruNode{email: email, hist: h, elem: elem}
		node.elem.Value = node
		c.uHist[email] = node
	} else {
		c.lruList.MoveToFront(node.elem)
	}
	node.hist.Up.Push(su)
	node.hist.Dn.Push(sd)
	if su > node.hist.PeakUp {
		node.hist.PeakUp = su
	}
	if sd > node.hist.PeakDn {
		node.hist.PeakDn = sd
	}
}

func (c *Collector) trackEvents(online []string, logIPs map[string]map[string]float64) {
	onlineSet := make(map[string]struct{}, len(online))
	for _, e := range online {
		onlineSet[e] = struct{}{}
	}

	// Detect connects (new in online)
	for email := range onlineSet {
		if _, wasPrev := c.prevOnline[email]; !wasPrev {
			ip := ""
			if ips, ok := logIPs[email]; ok {
				for k := range ips {
					ip = k
					break
				}
			}
			c.appendEvent(ConnEvent{Kind: "connect", Email: email, IP: ip, TS: time.Now()})
		}
	}

	// Detect disconnects
	for email := range c.prevOnline {
		if _, stillOnline := onlineSet[email]; !stillOnline {
			c.appendEvent(ConnEvent{Kind: "disconnect", Email: email, TS: time.Now()})
		}
	}

	c.prevOnline = onlineSet
}

func (c *Collector) appendEvent(ev ConnEvent) {
	c.Events = append(c.Events, ev)
	if len(c.Events) > 200 {
		c.Events = c.Events[len(c.Events)-200:]
	}
}

// UpHistSlice returns the last n global upload speed values (bytes/sec).
func (c *Collector) UpHistSlice(n int) []float64 {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.upHist.slice(n)
}

// DnHistSlice returns the last n global download speed values (bytes/sec).
func (c *Collector) DnHistSlice(n int) []float64 {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.dnHist.slice(n)
}

// GetUserHist returns the speed history for an email (nil if not tracked).
func (c *Collector) GetUserHist(email string) *UserHist {
	c.mu.Lock()
	defer c.mu.Unlock()
	if node, ok := c.uHist[email]; ok {
		return node.hist
	}
	return nil
}

// GetEvents returns a snapshot of connection events (newest last).
func (c *Collector) GetEvents() []ConnEvent {
	c.mu.Lock()
	defer c.mu.Unlock()
	out := make([]ConnEvent, len(c.Events))
	copy(out, c.Events)
	return out
}

// GetIPTraffic returns a snapshot of per-IP traffic.
func (c *Collector) GetIPTraffic() map[string]IPEntry {
	c.mu.Lock()
	defer c.mu.Unlock()
	out := make(map[string]IPEntry, len(c.IPTraffic))
	for ip, e := range c.IPTraffic {
		out[ip] = *e
	}
	return out
}
