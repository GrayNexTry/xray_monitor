// Package sysinfo collects system metrics via gopsutil.
package sysinfo

import (
	"os/exec"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/shirou/gopsutil/v3/cpu"
	"github.com/shirou/gopsutil/v3/disk"
	"github.com/shirou/gopsutil/v3/mem"
	psnet "github.com/shirou/gopsutil/v3/net"
	"github.com/shirou/gopsutil/v3/process"
)

// ProcEntry is one row from the top-processes list.
type ProcEntry struct {
	PID  int32
	Name string
	CPU  float64
	Mem  uint64
}

// SysData is a snapshot of all system metrics.
type SysData struct {
	CPUPct    float64
	RAMPct    float64
	RAMUsed   uint64
	RAMTotal  uint64
	SwapPct   float64
	SwapUsed  uint64
	SwapTotal uint64
	DiskPct   float64
	DiskUsed  uint64
	DiskTotal uint64
	RxPerSec  float64
	TxPerSec  float64
	RxTotal   uint64
	TxTotal   uint64
	TCPEst    int
	TCPListen int
	NumProcs  int
	XrayPID   int32
	XrayCPU   float64
	XrayMem   uint64
	TopProcs  []ProcEntry
	Hostname  string
	Error     string
}

type netSnap struct {
	rx uint64
	tx uint64
	ts time.Time
}

type tcpSnap struct {
	est    int
	listen int
	ts     time.Time
}

type procsSnap struct {
	procs []ProcEntry
	ts    time.Time
}

// Collector gathers system metrics periodically.
type Collector struct {
	mu        sync.RWMutex
	last      SysData
	netPrev   *netSnap
	tcpCache  tcpSnap
	procCache procsSnap
	xrayPID   int32
	xrayPIDT  time.Time

	// warm up CPU percentage
	cpuReady bool
}

// New creates a new system metrics collector.
func New() *Collector {
	c := &Collector{}
	// Prime CPU collection (first call always returns 0 on Linux)
	cpu.Percent(0, false) //nolint
	return c
}

// Collect gathers all metrics and stores them. Call from a background goroutine.
func (c *Collector) Collect() {
	data := SysData{}

	// CPU
	if pcts, err := cpu.Percent(0, false); err == nil && len(pcts) > 0 {
		data.CPUPct = pcts[0]
	}

	// Memory
	if vm, err := mem.VirtualMemory(); err == nil {
		data.RAMPct = vm.UsedPercent
		data.RAMUsed = vm.Used
		data.RAMTotal = vm.Total
	}
	if sm, err := mem.SwapMemory(); err == nil {
		data.SwapPct = sm.UsedPercent
		data.SwapUsed = sm.Used
		data.SwapTotal = sm.Total
	}

	// Disk
	if du, err := disk.Usage("/"); err == nil {
		data.DiskPct = du.UsedPercent
		data.DiskUsed = du.Used
		data.DiskTotal = du.Total
	}

	// Network IO rates
	if counters, err := psnet.IOCounters(false); err == nil && len(counters) > 0 {
		cnt := counters[0]
		data.RxTotal = cnt.BytesRecv
		data.TxTotal = cnt.BytesSent
		now := time.Now()
		c.mu.RLock()
		prev := c.netPrev
		c.mu.RUnlock()
		if prev != nil {
			dt := now.Sub(prev.ts).Seconds()
			if dt > 0 {
				data.RxPerSec = float64(cnt.BytesRecv-prev.rx) / dt
				data.TxPerSec = float64(cnt.BytesSent-prev.tx) / dt
			}
		}
		c.mu.Lock()
		c.netPrev = &netSnap{rx: cnt.BytesRecv, tx: cnt.BytesSent, ts: now}
		c.mu.Unlock()
	}

	// TCP connections (cached 15s)
	c.mu.RLock()
	tcpOk := time.Since(c.tcpCache.ts) < 15*time.Second
	c.mu.RUnlock()
	if tcpOk {
		c.mu.RLock()
		data.TCPEst = c.tcpCache.est
		data.TCPListen = c.tcpCache.listen
		c.mu.RUnlock()
	} else {
		if conns, err := psnet.Connections("tcp"); err == nil {
			for _, conn := range conns {
				switch conn.Status {
				case "ESTABLISHED":
					data.TCPEst++
				case "LISTEN":
					data.TCPListen++
				}
			}
		}
		c.mu.Lock()
		c.tcpCache = tcpSnap{est: data.TCPEst, listen: data.TCPListen, ts: time.Now()}
		c.mu.Unlock()
	}

	// Top processes (cached 10s)
	c.mu.RLock()
	procsOk := time.Since(c.procCache.ts) < 10*time.Second
	c.mu.RUnlock()
	if procsOk {
		c.mu.RLock()
		data.TopProcs = c.procCache.procs
		c.mu.RUnlock()
	} else {
		data.TopProcs = c.topProcs(12)
		c.mu.Lock()
		c.procCache = procsSnap{procs: data.TopProcs, ts: time.Now()}
		c.mu.Unlock()
	}

	// Xray process (cached 30s)
	c.mu.RLock()
	xrayOk := c.xrayPID > 0 && time.Since(c.xrayPIDT) < 30*time.Second
	xPID := c.xrayPID
	c.mu.RUnlock()
	if !xrayOk {
		xPID = c.findXrayPID()
		c.mu.Lock()
		c.xrayPID = xPID
		c.xrayPIDT = time.Now()
		c.mu.Unlock()
	}
	data.XrayPID = xPID
	if xPID > 0 {
		if p, err := process.NewProcess(xPID); err == nil {
			if pct, err := p.CPUPercent(); err == nil {
				data.XrayCPU = pct
			}
			if mi, err := p.MemoryInfo(); err == nil && mi != nil {
				data.XrayMem = mi.RSS
			}
		}
	}

	// Process count
	if pids, err := process.Pids(); err == nil {
		data.NumProcs = len(pids)
	}

	c.mu.Lock()
	c.last = data
	c.mu.Unlock()
}

// Get returns the most recently collected data (read-safe).
func (c *Collector) Get() SysData {
	c.mu.RLock()
	defer c.mu.RUnlock()
	return c.last
}

func (c *Collector) topProcs(n int) []ProcEntry {
	pids, err := process.Pids()
	if err != nil {
		return nil
	}
	entries := make([]ProcEntry, 0, len(pids))
	for _, pid := range pids {
		p, err := process.NewProcess(pid)
		if err != nil {
			continue
		}
		mi, err := p.MemoryInfo()
		if err != nil || mi == nil {
			continue
		}
		name, _ := p.Name()
		pct, _ := p.CPUPercent()
		entries = append(entries, ProcEntry{
			PID:  pid,
			Name: name,
			CPU:  pct,
			Mem:  mi.RSS,
		})
	}
	sort.Slice(entries, func(i, j int) bool {
		return entries[i].Mem > entries[j].Mem
	})
	if len(entries) > n {
		return entries[:n]
	}
	return entries
}

func (c *Collector) findXrayPID() int32 {
	pids, err := process.Pids()
	if err != nil {
		return 0
	}
	for _, pid := range pids {
		p, err := process.NewProcess(pid)
		if err != nil {
			continue
		}
		name, _ := p.Name()
		if strings.Contains(strings.ToLower(name), "xray") {
			return pid
		}
	}
	return 0
}

// Ping returns TCP round-trip latency to host:port in ms, -1 on failure.
func Ping(host string, timeout time.Duration) float64 {
	start := time.Now()
	cmd := exec.Command("ping", "-c", "1", "-W", "2", host)
	if err := cmd.Run(); err != nil {
		return -1
	}
	return float64(time.Since(start).Milliseconds())
}
