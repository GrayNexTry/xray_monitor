// Package sni classifies domain names into known service categories.
package sni

import (
	"regexp"
	"sync"
	"sync/atomic"
)

// Classification holds the result of domain classification.
type Classification struct {
	Tag      string
	Label    string
	ColorKey string // maps to a color constant in the TUI
}

type rule struct {
	rx    *regexp.Regexp
	class Classification
}

var services []rule

func init() {
	defs := []struct {
		pattern  string
		tag      string
		label    string
		colorKey string
	}{
		// Video streaming
		{`(?i)(googlevideo|youtube|ytimg|yt3|googlevideo\.com)`, "youtube", "YouTube", "red"},
		{`(?i)(netflix|nflx\.com|nfx\.com)`, "netflix", "Netflix", "red"},
		{`(?i)(twitch\.tv|twitchapps|jtvnw\.net|twitchstatic)`, "twitch", "Twitch", "mauve"},
		{`(?i)(primevideo|aiv-cdn|amazonvideo)`, "amazon", "Prime Video", "yellow"},
		{`(?i)(disney(plus|now)|dssott|hulu\.com|bamgrid)`, "disney", "Disney+", "blue"},
		{`(?i)(hbo(max|go)|wbd\.com|max\.com)`, "hbo", "HBO/Max", "mauve"},

		// Social / Messaging
		{`(?i)(t(elegram|coregram)\.org|telegram-cdn|tg\.dev|cdn(\.cdn)?\.telegram|api\.telegram)`, "telegram", "Telegram", "blue"},
		{`(?i)(discord(app|cdn)?\.com|discordstatic|discord\.gg)`, "discord", "Discord", "mauve"},
		{`(?i)(tiktok(cdn)?\.com|muscdn|musical\.ly|ibytedtos)`, "tiktok", "TikTok", "green"},
		{`(?i)(twitter\.com|x\.com|twimg|abs\.twimg|pbs\.twimg|t\.co)`, "twitter", "Twitter/X", "sky"},
		{`(?i)(instagram\.com|fb(cdn|static)|fbcdn|cdninstagram)`, "instagram", "Instagram", "peach"},
		{`(?i)(facebook\.com|fb\.com|fbcdn\.net)`, "facebook", "Facebook", "blue"},
		{`(?i)(vk\.com|userapi\.com|vkuseraudio|vkuserlive)`, "vk", "VK", "blue"},
		{`(?i)(whatsapp\.(com|net)|wa\.me)`, "whatsapp", "WhatsApp", "green"},
		{`(?i)(snapchat\.com|sc-cdn\.net|snap\.com)`, "snapchat", "Snapchat", "yellow"},

		// Music
		{`(?i)(spotify(cdn)?\.com|scdn\.co|spotimg)`, "spotify", "Spotify", "green"},
		{`(?i)(music\.yandex|yandex(cdn)?\.ru|apresolve\.spotify)`, "yandex_music", "Yandex Music", "red"},
		{`(?i)(soundcloud\.com|sndcdn\.com)`, "soundcloud", "SoundCloud", "peach"},

		// Gaming
		{`(?i)(valve|steamcontent|steampowered|steam-chat)`, "steam", "Steam", "sky"},
		{`(?i)(epicgames|unrealengine|epiccdn)`, "epic", "Epic Games", "sky"},
		{`(?i)(riotgames|leagueoflegends|valorant|lol\.gamescdn)`, "riot", "Riot Games", "red"},
		{`(?i)(ea\.com|origin\.com|ea-network)`, "ea", "EA/Origin", "peach"},
		{`(?i)(blizzard\.com|battle\.net|blzddist)`, "blizzard", "Blizzard", "blue"},
		{`(?i)(roblox(labs)?\.com|rbxcdn|rbxassetdelivery)`, "roblox", "Roblox", "sky"},

		// Cloud / CDN
		{`(?i)(cloudflare(-ipfs|-workers|\.com|\.net)|cdnjs\.cloudflare)`, "cloudflare", "Cloudflare", "peach"},
		{`(?i)(amazonaws\.com|aws\.amazon|cloudfront\.net|s3-|s3\.)`, "aws", "AWS", "yellow"},
		{`(?i)(akamai(hd|i|technologies|edge|mai)?\.(com|net)|akamaized|edgekey)`, "akamai", "Akamai", "yellow"},
		{`(?i)(gstatic|googleapiscdn|googleapis\.com|googlecdn)`, "gcp", "Google Cloud", "blue"},
		{`(?i)(fastly\.com|fastly-\.net|fastlylb)`, "fastly", "Fastly", "peach"},

		// Big tech infra
		{`(?i)(apple\.com|icloud|apple-cdn|aaplimg|appleid)`, "apple", "Apple", "subtext"},
		{`(?i)(microsoft\.(com|net)|msftconnecttest|windows\.com|office365|msecnd\.net|akadns\.net)`, "microsoft", "Microsoft", "blue"},
		{`(?i)(openai\.com|chatgpt|api\.openai)`, "openai", "OpenAI", "teal"},
		{`(?i)(anthropic\.com|claude\.ai)`, "anthropic", "Anthropic", "mauve"},

		// Torrents / P2P
		{`(?i)(torrent|announce|tracker\.|pieces\.)`, "torrent", "BitTorrent", "warn"},

		// Google generic (lower priority than specific Google services)
		{`(?i)(google\.(com|de|fr|ru|co|net)|googlesyndication|googletagmanager|gvt[12]\.)`, "google", "Google", "blue"},
	}

	for _, d := range defs {
		services = append(services, rule{
			rx:    regexp.MustCompile(d.pattern),
			class: Classification{Tag: d.tag, Label: d.label, ColorKey: d.colorKey},
		})
	}
}

var (
	classCache    sync.Map
	cacheSizeCur  atomic.Int64
	cacheSizeLimit int64 = 5000
	noMatch              = &Classification{Tag: "?", Label: "Domain", ColorKey: "dim"}
)

// Classify returns the service classification for a domain, or nil if unknown.
func Classify(domain string) *Classification {
	if v, ok := classCache.Load(domain); ok {
		c := v.(*Classification)
		if c == noMatch {
			return nil
		}
		return c
	}
	for i := range services {
		if services[i].rx.MatchString(domain) {
			c := &services[i].class
			if cacheSizeCur.Load() < cacheSizeLimit {
				classCache.Store(domain, c)
				cacheSizeCur.Add(1)
			}
			return c
		}
	}
	if cacheSizeCur.Load() < cacheSizeLimit {
		classCache.Store(domain, noMatch)
		cacheSizeCur.Add(1)
	}
	return nil
}
