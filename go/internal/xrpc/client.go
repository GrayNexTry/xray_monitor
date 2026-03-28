// Package xrpc implements an Xray gRPC stats client using raw h2c HTTP/2
// and manual protobuf wire encoding — no protoc or grpc-go library needed.
package xrpc

import (
	"bytes"
	"context"
	"crypto/tls"
	"encoding/binary"
	"fmt"
	"io"
	"net"
	"net/http"
	"sync"
	"time"

	"golang.org/x/net/http2"
)

const (
	basePath = "/xray.app.stats.command.StatsService/"
	timeout  = 8 * time.Second
)

// StatEntry is one counter returned by QueryStats.
type StatEntry struct {
	Name  string
	Value int64
}

// SysStats is the Xray process stats returned by GetSysStats.
type SysStats struct {
	Goroutines  uint64
	GCRuns      uint64
	Alloc       uint64
	TotalAlloc  uint64
	Sys         uint64
	Mallocs     uint64
	Frees       uint64
	LiveObjects uint64
	PauseNs     uint64
	Uptime      uint64 // seconds
}

// Client is a thread-safe Xray gRPC client.
type Client struct {
	baseURL string
	hc      *http.Client
	mu      sync.Mutex
	healthy bool
}

var bufPool = sync.Pool{New: func() any { return new(bytes.Buffer) }}

// New creates a new client targeting addr (host:port, no scheme).
func New(addr string) *Client {
	t := &http2.Transport{
		AllowHTTP: true,
		DialTLSContext: func(ctx context.Context, network, addr string, _ *tls.Config) (net.Conn, error) {
			return (&net.Dialer{Timeout: 5 * time.Second}).DialContext(ctx, network, addr)
		},
	}
	return &Client{
		baseURL: "http://" + addr,
		hc:      &http.Client{Transport: t, Timeout: timeout},
		healthy: true,
	}
}

func (c *Client) IsHealthy() bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.healthy
}

// call makes one gRPC call: POST /<method>, body = gRPC frame(protoBytes).
// Returns the deframed protobuf payload of the response.
func (c *Client) call(ctx context.Context, method string, body []byte) ([]byte, error) {
	buf := bufPool.Get().(*bytes.Buffer)
	buf.Reset()
	defer bufPool.Put(buf)

	// gRPC frame: [0x00][length uint32 BE][body...]
	frame := make([]byte, 5+len(body))
	frame[0] = 0
	binary.BigEndian.PutUint32(frame[1:5], uint32(len(body)))
	copy(frame[5:], body)

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.baseURL+basePath+method, bytes.NewReader(frame))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/grpc")
	req.Header.Set("TE", "trailers")

	resp, err := c.hc.Do(req)
	if err != nil {
		c.mu.Lock()
		c.healthy = false
		c.mu.Unlock()
		return nil, fmt.Errorf("grpc %s: %w", method, err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != 200 {
		return nil, fmt.Errorf("grpc %s: HTTP %d", method, resp.StatusCode)
	}

	data, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("grpc %s read: %w", method, err)
	}

	// Check gRPC trailer status
	grpcStatus := resp.Trailer.Get("grpc-status")
	if grpcStatus != "" && grpcStatus != "0" {
		msg := resp.Trailer.Get("grpc-message")
		return nil, fmt.Errorf("grpc %s status=%s: %s", method, grpcStatus, msg)
	}

	if len(data) < 5 {
		c.mu.Lock()
		c.healthy = false
		c.mu.Unlock()
		return nil, fmt.Errorf("grpc %s: short response (%d bytes)", method, len(data))
	}
	msgLen := binary.BigEndian.Uint32(data[1:5])
	if int(msgLen) > len(data)-5 {
		return nil, fmt.Errorf("grpc %s: truncated payload", method)
	}

	c.mu.Lock()
	c.healthy = true
	c.mu.Unlock()
	return data[5 : 5+msgLen], nil
}

// QueryStats fetches all traffic stats matching pattern.
// Set reset=true to reset counters after reading.
func (c *Client) QueryStats(ctx context.Context, pattern string, reset bool) ([]StatEntry, error) {
	req := encodeQueryStatsReq(pattern, reset)
	data, err := c.call(ctx, "QueryStats", req)
	if err != nil {
		return nil, err
	}
	return decodeQueryStatsResp(data)
}

// GetSysStats fetches Xray process memory/goroutine stats.
func (c *Client) GetSysStats(ctx context.Context) (SysStats, error) {
	data, err := c.call(ctx, "GetSysStats", nil)
	if err != nil {
		return SysStats{}, err
	}
	return decodeSysStats(data)
}

// GetAllOnlineUsers returns stat_names for currently connected users.
func (c *Client) GetAllOnlineUsers(ctx context.Context) ([]string, error) {
	data, err := c.call(ctx, "GetAllOnlineUsers", nil)
	if err != nil {
		return nil, err
	}
	return decodeStringList(data, 1)
}

// GetOnlineIPs returns a map of IP → timestamp for a given stat name.
func (c *Client) GetOnlineIPs(ctx context.Context, statName string) (map[string]int64, error) {
	req := encodeString(1, statName)
	data, err := c.call(ctx, "GetStatsOnlineIpList", req)
	if err != nil {
		return nil, err
	}
	return decodeIPMap(data)
}

// ─── Protobuf Wire Encoding ───────────────────────────────────────────────────

func appendVarint(b []byte, v uint64) []byte {
	for v >= 0x80 {
		b = append(b, byte(v)|0x80)
		v >>= 7
	}
	return append(b, byte(v))
}

// consumeVarint decodes a varint from b. Returns value and bytes consumed (-1 on error).
func consumeVarint(b []byte) (uint64, int) {
	var x uint64
	for i, c := range b {
		if i == 10 {
			return 0, -1
		}
		x |= uint64(c&0x7f) << (7 * uint(i))
		if c < 0x80 {
			return x, i + 1
		}
	}
	return 0, -1
}

// encodeString encodes a string as protobuf field (wire type 2).
func encodeString(fieldNum int, s string) []byte {
	tag := uint64(fieldNum)<<3 | 2
	var b []byte
	b = appendVarint(b, tag)
	b = appendVarint(b, uint64(len(s)))
	return append(b, s...)
}

// encodeBool encodes a bool as protobuf field (wire type 0, only if true).
func encodeBool(fieldNum int, v bool) []byte {
	if !v {
		return nil
	}
	tag := uint64(fieldNum)<<3 | 0
	var b []byte
	b = appendVarint(b, tag)
	return append(b, 1)
}

func encodeQueryStatsReq(pattern string, reset bool) []byte {
	var b []byte
	b = append(b, encodeString(1, pattern)...)
	b = append(b, encodeBool(2, reset)...)
	return b
}

// ─── Protobuf Wire Decoding ───────────────────────────────────────────────────

// nextField reads the next tag+value from b. Returns fieldNum, wireType, payload, remaining.
// payload is the raw bytes of the value (for wire type 2: the content; for wire type 0: nil, use varint).
func nextField(b []byte) (fieldNum int, wireType int, varVal uint64, payload []byte, rest []byte, ok bool) {
	if len(b) == 0 {
		return 0, 0, 0, nil, nil, false
	}
	tag, n := consumeVarint(b)
	if n <= 0 {
		return 0, 0, 0, nil, nil, false
	}
	b = b[n:]
	fieldNum = int(tag >> 3)
	wireType = int(tag & 0x7)
	switch wireType {
	case 0: // varint
		v, n := consumeVarint(b)
		if n <= 0 {
			return 0, 0, 0, nil, nil, false
		}
		return fieldNum, wireType, v, nil, b[n:], true
	case 1: // 64-bit fixed
		if len(b) < 8 {
			return 0, 0, 0, nil, nil, false
		}
		return fieldNum, wireType, 0, b[:8], b[8:], true
	case 2: // length-delimited
		l, n := consumeVarint(b)
		if n <= 0 || int(l) > len(b)-n {
			return 0, 0, 0, nil, nil, false
		}
		return fieldNum, wireType, 0, b[n : n+int(l)], b[n+int(l):], true
	case 5: // 32-bit fixed
		if len(b) < 4 {
			return 0, 0, 0, nil, nil, false
		}
		return fieldNum, wireType, 0, b[:4], b[4:], true
	default:
		return 0, 0, 0, nil, nil, false
	}
}

func decodeQueryStatsResp(data []byte) ([]StatEntry, error) {
	var entries []StatEntry
	for len(data) > 0 {
		fnum, wtype, _, payload, rest, ok := nextField(data)
		if !ok {
			break
		}
		data = rest
		if fnum == 1 && wtype == 2 {
			e, err := decodeStat(payload)
			if err == nil {
				entries = append(entries, e)
			}
		}
	}
	return entries, nil
}

func decodeStat(data []byte) (StatEntry, error) {
	var s StatEntry
	for len(data) > 0 {
		fnum, wtype, varVal, payload, rest, ok := nextField(data)
		if !ok {
			break
		}
		data = rest
		switch {
		case fnum == 1 && wtype == 2:
			s.Name = string(payload)
		case fnum == 2 && wtype == 0:
			s.Value = int64(varVal)
		}
	}
	return s, nil
}

func decodeSysStats(data []byte) (SysStats, error) {
	var s SysStats
	for len(data) > 0 {
		fnum, wtype, varVal, _, rest, ok := nextField(data)
		if !ok {
			break
		}
		data = rest
		if wtype != 0 {
			continue
		}
		switch fnum {
		case 1:
			s.Goroutines = varVal
		case 2:
			s.GCRuns = varVal
		case 3:
			s.Alloc = varVal
		case 4:
			s.TotalAlloc = varVal
		case 5:
			s.Sys = varVal
		case 6:
			s.Mallocs = varVal
		case 7:
			s.Frees = varVal
		case 8:
			s.LiveObjects = varVal
		case 9:
			s.PauseNs = varVal
		case 10:
			s.Uptime = varVal
		}
	}
	return s, nil
}

func decodeStringList(data []byte, fieldNum int) ([]string, error) {
	var out []string
	for len(data) > 0 {
		fnum, wtype, _, payload, rest, ok := nextField(data)
		if !ok {
			break
		}
		data = rest
		if fnum == fieldNum && wtype == 2 {
			out = append(out, string(payload))
		}
	}
	return out, nil
}

// decodeIPMap decodes GetStatsOnlineIpListResponse.ip_list (map<string,int64>).
// Proto encodes maps as repeated message{key(f1,str), value(f2,int64)}.
func decodeIPMap(data []byte) (map[string]int64, error) {
	out := make(map[string]int64)
	for len(data) > 0 {
		fnum, wtype, _, payload, rest, ok := nextField(data)
		if !ok {
			break
		}
		data = rest
		if fnum == 1 && wtype == 2 {
			// map entry message
			var key string
			var val int64
			entry := payload
			for len(entry) > 0 {
				ef, ew, ev, ep, er, eok := nextField(entry)
				if !eok {
					break
				}
				entry = er
				switch {
				case ef == 1 && ew == 2:
					key = string(ep)
				case ef == 2 && ew == 0:
					val = int64(ev)
				}
			}
			if key != "" {
				out[key] = val
			}
		}
	}
	return out, nil
}
