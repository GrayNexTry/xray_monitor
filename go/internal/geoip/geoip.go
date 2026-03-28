// Package geoip provides IP geolocation via MaxMind MMDB (offline) or ip-api.com (online).
package geoip

import (
	"context"
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"sync"
	"time"

	"github.com/oschwald/maxminddb-golang"
)

// Info holds geolocation data for an IP.
type Info struct {
	CC      string
	Country string
	City    string
	ISP     string
	ASN     string
	ASName  string
	Hosting bool
}

const (
	cacheMax     = 2000
	offlineTTL   = 7 * 24 * time.Hour
	onlineTTL    = 1 * time.Hour
	pendingTTL   = 30 * time.Second
	apiURL       = "http://ip-api.com/json/%s?fields=status,country,countryCode,city,isp,as,asname,hosting"
	apiRateLimit = 45 // requests per minute
)

var mmdbPaths = []string{
	"/opt/xray-monitor/GeoLite2-City.mmdb",
	"/usr/share/GeoIP/GeoLite2-City.mmdb",
	"/var/lib/GeoIP/GeoLite2-City.mmdb",
	"/etc/GeoIP/GeoLite2-City.mmdb",
}

type cacheEntry struct {
	info Info
	ts   time.Time
	ttl  time.Duration
}

// GeoIP is a thread-safe geolocation resolver.
type GeoIP struct {
	cityDB  *maxminddb.Reader
	offline bool

	mu       sync.Mutex
	cache    map[string]*cacheEntry
	cacheQ   []string // insertion order for eviction
	pending  map[string]time.Time

	sem  chan struct{} // concurrency limiter
	hc   *http.Client

	// rate limiter: track when each minute resets
	apiCalls   int
	apiResetAt time.Time
}

// New creates a GeoIP resolver. Opens MaxMind database if available.
func New() *GeoIP {
	g := &GeoIP{
		cache:   make(map[string]*cacheEntry),
		pending: make(map[string]time.Time),
		sem:     make(chan struct{}, 5),
		hc:      &http.Client{Timeout: 8 * time.Second},
	}
	for _, path := range mmdbPaths {
		if db, err := maxminddb.Open(path); err == nil {
			g.cityDB = db
			g.offline = true
			break
		}
	}
	return g
}

// Lookup returns cached geolocation info for ip, or nil if still pending.
// For unknown IPs it triggers an async lookup.
func (g *GeoIP) Lookup(ip string) *Info {
	if g.isLocal(ip) {
		info := &Info{CC: "LO", Country: "Local"}
		return info
	}

	g.mu.Lock()
	defer g.mu.Unlock()

	if e, ok := g.cache[ip]; ok {
		if time.Since(e.ts) < e.ttl {
			return &e.info
		}
		// expired — evict and re-fetch
		delete(g.cache, ip)
	}

	if fetchAt, pend := g.pending[ip]; pend {
		if time.Since(fetchAt) < pendingTTL {
			return nil // still pending
		}
		delete(g.pending, ip)
	}

	// Queue async fetch
	g.pending[ip] = time.Now()
	go g.fetch(ip)
	return nil
}

// Fmt returns a compact country+city string, "..." if still pending.
func (g *GeoIP) Fmt(ip string) string {
	info := g.Lookup(ip)
	if info == nil {
		return "..."
	}
	if info.City != "" {
		return fmt.Sprintf("%s %s", info.CC, info.City)
	}
	return info.Country
}

// Backend returns "MaxMind" or "ip-api.com".
func (g *GeoIP) Backend() string {
	if g.offline {
		return "MaxMind"
	}
	return "ip-api.com"
}

func (g *GeoIP) isLocal(ip string) bool {
	parsed := net.ParseIP(ip)
	if parsed == nil {
		return false
	}
	return parsed.IsLoopback() || parsed.IsPrivate()
}

func (g *GeoIP) fetch(ip string) {
	var info Info
	var ttl time.Duration

	if g.offline {
		if i := g.lookupMMDB(ip); i != nil {
			info = *i
			ttl = offlineTTL
		}
	} else {
		g.sem <- struct{}{}
		defer func() { <-g.sem }()

		if i, err := g.lookupAPI(ip); err == nil {
			info = *i
			ttl = onlineTTL
		}
	}

	g.mu.Lock()
	defer g.mu.Unlock()
	delete(g.pending, ip)
	if ttl == 0 {
		ttl = 10 * time.Minute // negative cache
	}
	g.cacheSet(ip, info, ttl)
}

func (g *GeoIP) lookupMMDB(ip string) *Info {
	if g.cityDB == nil {
		return nil
	}
	parsed := net.ParseIP(ip)
	if parsed == nil {
		return nil
	}

	var record struct {
		Country struct {
			ISOCode string            `maxminddb:"iso_code"`
			Names   map[string]string `maxminddb:"names"`
		} `maxminddb:"country"`
		City struct {
			Names map[string]string `maxminddb:"names"`
		} `maxminddb:"city"`
	}
	if err := g.cityDB.Lookup(parsed, &record); err != nil {
		return nil
	}
	info := &Info{
		CC:      record.Country.ISOCode,
		Country: record.Country.Names["en"],
		City:    record.City.Names["en"],
	}
	return info
}

type apiResponse struct {
	Status      string  `json:"status"`
	Country     string  `json:"country"`
	CountryCode string  `json:"countryCode"`
	City        string  `json:"city"`
	ISP         string  `json:"isp"`
	AS          string  `json:"as"`
	ASName      string  `json:"asname"`
	Hosting     bool    `json:"hosting"`
}

func (g *GeoIP) lookupAPI(ip string) (*Info, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 8*time.Second)
	defer cancel()

	req, _ := http.NewRequestWithContext(ctx, http.MethodGet, fmt.Sprintf(apiURL, ip), nil)
	resp, err := g.hc.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	var ar apiResponse
	if err := json.NewDecoder(resp.Body).Decode(&ar); err != nil {
		return nil, err
	}
	if ar.Status != "success" {
		return nil, fmt.Errorf("api status: %s", ar.Status)
	}
	return &Info{
		CC:      ar.CountryCode,
		Country: ar.Country,
		City:    ar.City,
		ISP:     ar.ISP,
		ASN:     ar.AS,
		ASName:  ar.ASName,
		Hosting: ar.Hosting,
	}, nil
}

// cacheSet adds an entry; must hold g.mu.
func (g *GeoIP) cacheSet(ip string, info Info, ttl time.Duration) {
	if len(g.cache) >= cacheMax {
		// evict oldest
		if len(g.cacheQ) > 0 {
			oldest := g.cacheQ[0]
			g.cacheQ = g.cacheQ[1:]
			delete(g.cache, oldest)
		}
	}
	g.cache[ip] = &cacheEntry{info: info, ts: time.Now(), ttl: ttl}
	g.cacheQ = append(g.cacheQ, ip)
}

// Close releases the MaxMind database.
func (g *GeoIP) Close() {
	if g.cityDB != nil {
		g.cityDB.Close()
	}
}
