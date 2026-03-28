// Package config parses an Xray config.json and builds client connection URLs.
package config

import (
	"encoding/base64"
	"encoding/json"
	"fmt"
	"net/url"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/graynextry/xray-monitor/internal/xcrypto"
)

// ClientURL holds everything needed to connect as a VPN client.
type ClientURL struct {
	Email    string
	UUID     string
	Tag      string
	Port     int
	Protocol string // vless, vmess, trojan, shadowsocks
	Network  string // tcp, ws, h2, grpc
	Security string // none, tls, reality
	Flow     string
	SNI      string
	URL      string
}

// XrayConfig wraps the parsed Xray config with mtime-based caching.
type XrayConfig struct {
	path  string
	mu    sync.RWMutex
	mtime time.Time
	raw   map[string]any
}

// New creates a config reader for the given path.
func New(path string) *XrayConfig { return &XrayConfig{path: path} }

// load re-reads the file if it changed on disk.
func (c *XrayConfig) load() (map[string]any, error) {
	info, err := os.Stat(c.path)
	if err != nil {
		return nil, err
	}
	c.mu.RLock()
	if !info.ModTime().After(c.mtime) && c.raw != nil {
		raw := c.raw
		c.mu.RUnlock()
		return raw, nil
	}
	c.mu.RUnlock()

	f, err := os.ReadFile(c.path)
	if err != nil {
		return nil, err
	}
	var raw map[string]any
	if err := json.Unmarshal(f, &raw); err != nil {
		return nil, fmt.Errorf("config parse: %w", err)
	}

	c.mu.Lock()
	c.raw = raw
	c.mtime = info.ModTime()
	c.mu.Unlock()
	return raw, nil
}

// GetInbounds returns the inbounds slice from config.
func (c *XrayConfig) GetInbounds() []map[string]any {
	raw, err := c.load()
	if err != nil {
		return nil
	}
	inbounds, _ := raw["inbounds"].([]any)
	out := make([]map[string]any, 0, len(inbounds))
	for _, v := range inbounds {
		if m, ok := v.(map[string]any); ok {
			out = append(out, m)
		}
	}
	return out
}

// BuildClientURLs constructs connection URLs for all clients in all inbounds.
func (c *XrayConfig) BuildClientURLs(serverIP string) []ClientURL {
	if serverIP == "" {
		serverIP = "YOUR_SERVER_IP"
	}
	var out []ClientURL
	for _, ib := range c.GetInbounds() {
		proto, _ := ib["protocol"].(string)
		if proto == "" {
			continue
		}
		tag, _ := ib["tag"].(string)
		port := int(asFloat(ib["port"]))

		settings, _ := ib["settings"].(map[string]any)
		ss, _ := ib["streamSettings"].(map[string]any)

		network := "tcp"
		if ss != nil {
			if n, ok := ss["network"].(string); ok && n != "" {
				network = n
			}
		}
		security := "none"
		if ss != nil {
			if s, ok := ss["security"].(string); ok && s != "" {
				security = s
			}
		}

		switch proto {
		case "vless", "vmess", "trojan":
			clients := getClients(settings)
			for _, cl := range clients {
				u, err := buildURL(proto, cl, ib, ss, serverIP, port, tag, network, security)
				if err != nil {
					continue
				}
				email, _ := cl["email"].(string)
				uid, _ := cl["id"].(string)
				flow, _ := cl["flow"].(string)
				sni := extractSNI(ss, security)
				out = append(out, ClientURL{
					Email:    email,
					UUID:     uid,
					Tag:      tag,
					Port:     port,
					Protocol: proto,
					Network:  network,
					Security: security,
					Flow:     flow,
					SNI:      sni,
					URL:      u,
				})
			}
		case "shadowsocks":
			method, _ := settings["method"].(string)
			password, _ := settings["password"].(string)
			email, _ := settings["email"].(string)
			if email == "" {
				email = tag
			}
			if method != "" && password != "" {
				auth := base64.StdEncoding.EncodeToString([]byte(method + ":" + password))
				u := fmt.Sprintf("ss://%s@%s:%d#%s", auth, serverIP, port, url.PathEscape(email))
				out = append(out, ClientURL{
					Email: email, Tag: tag, Port: port,
					Protocol: "shadowsocks", Network: network, URL: u,
				})
			}
		}
	}
	return out
}

func getClients(settings map[string]any) []map[string]any {
	if settings == nil {
		return nil
	}
	raw, _ := settings["clients"].([]any)
	out := make([]map[string]any, 0, len(raw))
	for _, v := range raw {
		if m, ok := v.(map[string]any); ok {
			out = append(out, m)
		}
	}
	return out
}

func extractSNI(ss map[string]any, security string) string {
	if ss == nil {
		return ""
	}
	var cfg map[string]any
	switch security {
	case "tls":
		cfg, _ = ss["tlsSettings"].(map[string]any)
	case "reality":
		cfg, _ = ss["realitySettings"].(map[string]any)
	}
	if cfg == nil {
		return ""
	}
	sni, _ := cfg["serverName"].(string)
	if sni == "" {
		if sns, ok := cfg["serverNames"].([]any); ok && len(sns) > 0 {
			sni, _ = sns[0].(string)
		}
	}
	return sni
}

func buildURL(proto string, client, ib, ss map[string]any, serverIP string, port int, tag, network, security string) (string, error) {
	id, _ := client["id"].(string)
	if id == "" {
		if p, ok := client["password"].(string); ok {
			id = p
		}
	}
	email, _ := client["email"].(string)
	fragment := url.PathEscape(email)
	if fragment == "" {
		fragment = tag
	}
	params := url.Values{}
	params.Set("type", network)

	// Stream-specific params
	if ss != nil {
		switch network {
		case "ws":
			if wsCfg, ok := ss["wsSettings"].(map[string]any); ok {
				if path, ok := wsCfg["path"].(string); ok && path != "" {
					params.Set("path", path)
				}
				if hdrs, ok := wsCfg["headers"].(map[string]any); ok {
					if h, ok := hdrs["Host"].(string); ok {
						params.Set("host", h)
					}
				}
			}
		case "grpc":
			if grpcCfg, ok := ss["grpcSettings"].(map[string]any); ok {
				if svc, ok := grpcCfg["serviceName"].(string); ok {
					params.Set("serviceName", svc)
				}
			}
		case "h2":
			if h2Cfg, ok := ss["httpSettings"].(map[string]any); ok {
				if hosts, ok := h2Cfg["host"].([]any); ok && len(hosts) > 0 {
					if h, ok := hosts[0].(string); ok {
						params.Set("host", h)
					}
				}
				if path, ok := h2Cfg["path"].(string); ok {
					params.Set("path", path)
				}
			}
		}
	}

	// Security params
	switch security {
	case "tls":
		params.Set("security", "tls")
		tlsCfg, _ := ss["tlsSettings"].(map[string]any)
		if tlsCfg != nil {
			if sni, ok := tlsCfg["serverName"].(string); ok {
				params.Set("sni", sni)
			}
			if fp, ok := tlsCfg["fingerprint"].(string); ok {
				params.Set("fp", fp)
			}
			if alpn, ok := tlsCfg["alpn"].([]any); ok && len(alpn) > 0 {
				parts := make([]string, len(alpn))
				for i, a := range alpn {
					parts[i], _ = a.(string)
				}
				params.Set("alpn", strings.Join(parts, ","))
			}
		}
	case "reality":
		params.Set("security", "reality")
		realCfg, _ := ss["realitySettings"].(map[string]any)
		if realCfg != nil {
			if sni, ok := realCfg["serverName"].(string); ok {
				params.Set("sni", sni)
			} else if sns, ok := realCfg["serverNames"].([]any); ok && len(sns) > 0 {
				params.Set("sni", sns[0].(string))
			}
			if fp, ok := realCfg["fingerprint"].(string); ok {
				params.Set("fp", fp)
			}
			// Derive public key from private key
			if privKey, ok := realCfg["privateKey"].(string); ok {
				pubKey, err := xcrypto.DerivePublicKey(privKey)
				if err == nil {
					params.Set("pbk", pubKey)
				}
			} else if pubKey, ok := realCfg["publicKey"].(string); ok {
				params.Set("pbk", pubKey)
			}
			// Short ID from client or config
			if sids, ok := client["shortIds"].([]any); ok && len(sids) > 0 {
				params.Set("sid", sids[0].(string))
			} else if sids, ok := realCfg["shortIds"].([]any); ok && len(sids) > 0 {
				params.Set("sid", sids[0].(string))
			}
			if spx, ok := realCfg["spiderX"].(string); ok && spx != "" {
				params.Set("spx", spx)
			}
		}
	}

	// Flow
	if flow, ok := client["flow"].(string); ok && flow != "" {
		params.Set("flow", flow)
	}

	switch proto {
	case "vless":
		return fmt.Sprintf("vless://%s@%s:%d?%s#%s", id, serverIP, port, params.Encode(), fragment), nil
	case "vmess":
		obj := map[string]any{
			"v": "2", "ps": email, "add": serverIP, "port": strconv.Itoa(port),
			"id": id, "aid": "0", "net": network, "type": "none",
			"host": params.Get("host"), "path": params.Get("path"),
			"tls": map[string]string{"none": "", "tls": "tls", "reality": "reality"}[security],
		}
		b, _ := json.Marshal(obj)
		return "vmess://" + base64.StdEncoding.EncodeToString(b), nil
	case "trojan":
		return fmt.Sprintf("trojan://%s@%s:%d?%s#%s", id, serverIP, port, params.Encode(), fragment), nil
	}
	return "", fmt.Errorf("unsupported protocol: %s", proto)
}

// Path returns the config file path.
func (c *XrayConfig) Path() string { return c.path }

func asFloat(v any) float64 {
	switch x := v.(type) {
	case float64:
		return x
	case int:
		return float64(x)
	case int64:
		return float64(x)
	}
	return 0
}

