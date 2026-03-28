// Package storage persists daily traffic history to a JSON file.
package storage

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/graynextry/xray-monitor/internal/stats"
)

const (
	saveEvery = 30   // ticks between auto-saves
	keepDays  = 90   // how many days of history to keep
)

// TrafficEntry holds one day's traffic for a user.
type TrafficEntry struct {
	Up int64 `json:"up"`
	Dn int64 `json:"dn"`
}

// BaseEntry tracks the running day's baseline counters.
type BaseEntry struct {
	UpAbs int64 `json:"up_abs"` // xray absolute counter snapshot
	DnAbs int64 `json:"dn_abs"`
	PreUp int64 `json:"pre_up"` // accumulated from previous restarts
	PreDn int64 `json:"pre_dn"`
}

type fileData struct {
	Daily map[string]TrafficEntry `json:"daily"` // "email:YYYY-MM-DD"
	Bases map[string]BaseEntry    `json:"bases"` // email
}

// Storage manages traffic persistence.
type Storage struct {
	path      string
	mu        sync.Mutex
	data      fileData
	todayDate string
	tickN     int
}

// New loads (or creates) the storage file at path.
func New(path string) (*Storage, error) {
	s := &Storage{
		path:      path,
		todayDate: today(),
		data: fileData{
			Daily: make(map[string]TrafficEntry),
			Bases: make(map[string]BaseEntry),
		},
	}
	if err := os.MkdirAll(filepath.Dir(path), 0755); err != nil {
		return nil, err
	}
	_ = s.load() // ignore error on first run
	return s, nil
}

func today() string {
	return time.Now().Format("2006-01-02")
}

func (s *Storage) load() error {
	data, err := os.ReadFile(s.path)
	if err != nil {
		return err
	}
	var fd fileData
	if err := json.Unmarshal(data, &fd); err != nil {
		return err
	}
	if fd.Daily == nil {
		fd.Daily = make(map[string]TrafficEntry)
	}
	if fd.Bases == nil {
		fd.Bases = make(map[string]BaseEntry)
	}
	s.data = fd
	return nil
}

func (s *Storage) save() error {
	data, err := json.MarshalIndent(s.data, "", "  ")
	if err != nil {
		return err
	}
	tmp := s.path + ".tmp"
	if err := os.WriteFile(tmp, data, 0644); err != nil {
		return err
	}
	return os.Rename(tmp, s.path)
}

// Update processes a new set of absolute user counters from stats.
// usersAbs maps email → BucketBytes (absolute from Xray).
func (s *Storage) Update(usersAbs map[string]stats.BucketBytes) {
	s.mu.Lock()
	defer s.mu.Unlock()

	now := today()
	if now != s.todayDate {
		s.rotateDay()
		s.todayDate = now
	}

	for email, bb := range usersAbs {
		base, hasBase := s.data.Bases[email]

		if !hasBase {
			// First time seeing this user — establish baseline
			base = BaseEntry{UpAbs: bb.Up, DnAbs: bb.Dn}
			s.data.Bases[email] = base
			continue
		}

		// Detect xray restart (counters went backward)
		if bb.Up < base.UpAbs {
			base.PreUp += base.UpAbs - base.UpAbs // accumulate pre-restart: old high water
			base.PreUp = base.UpAbs               // restart point
			base.UpAbs = bb.Up
		}
		if bb.Dn < base.DnAbs {
			base.PreDn = base.DnAbs
			base.DnAbs = bb.Dn
		}

		// Compute today's traffic
		deltaUp := bb.Up - base.UpAbs
		deltaDn := bb.Dn - base.DnAbs
		if deltaUp < 0 {
			deltaUp = 0
		}
		if deltaDn < 0 {
			deltaDn = 0
		}
		todayUp := base.PreUp + deltaUp
		todayDn := base.PreDn + deltaDn

		key := email + ":" + s.todayDate
		s.data.Daily[key] = TrafficEntry{Up: todayUp, Dn: todayDn}
		s.data.Bases[email] = base
	}

	s.tickN++
	if s.tickN%saveEvery == 0 {
		_ = s.save()
	}
}

func (s *Storage) rotateDay() {
	cutoff := time.Now().AddDate(0, 0, -keepDays).Format("2006-01-02")
	for key := range s.data.Daily {
		// key format: "email:YYYY-MM-DD"
		idx := strings.LastIndex(key, ":")
		if idx < 0 {
			continue
		}
		date := key[idx+1:]
		if date < cutoff {
			delete(s.data.Daily, key)
		}
	}
	// Reset bases for new day (pre-values become new pre)
	for email, base := range s.data.Bases {
		_ = email
		base.PreUp = 0
		base.PreDn = 0
		s.data.Bases[email] = base
	}
}

// GetToday returns today's traffic per user.
func (s *Storage) GetToday() map[string]TrafficEntry {
	s.mu.Lock()
	defer s.mu.Unlock()
	d := s.todayDate
	out := make(map[string]TrafficEntry)
	for key, entry := range s.data.Daily {
		idx := strings.LastIndex(key, ":")
		if idx < 0 {
			continue
		}
		if key[idx+1:] == d {
			out[key[:idx]] = entry
		}
	}
	return out
}

// GetPeriod returns aggregated traffic per user for the last nDays days.
func (s *Storage) GetPeriod(nDays int) map[string]TrafficEntry {
	s.mu.Lock()
	defer s.mu.Unlock()
	cutoff := time.Now().AddDate(0, 0, -nDays).Format("2006-01-02")
	out := make(map[string]TrafficEntry)
	for key, entry := range s.data.Daily {
		idx := strings.LastIndex(key, ":")
		if idx < 0 {
			continue
		}
		date := key[idx+1:]
		if date >= cutoff {
			email := key[:idx]
			e := out[email]
			e.Up += entry.Up
			e.Dn += entry.Dn
			out[email] = e
		}
	}
	return out
}

// Flush forces an immediate write to disk.
func (s *Storage) Flush() error {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.save()
}

// Close flushes and releases resources.
func (s *Storage) Close() error { return s.Flush() }
