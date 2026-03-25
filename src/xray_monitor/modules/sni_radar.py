"""SNI Radar: классифицирует SNI/dest-домены по известным сервисам.

При включённом sniffing в Xray access.log появляются записи вида:
  1.2.3.4:54321 accepted ... email: user@tag -> rr1.googlevideo.com:443
LogTail парсит эти строки и заполняет ip_sni[ip] = deque([(domain, ts), ...]).
classify(domain) возвращает (tag, label, color_key) для Rich-рендера.
"""

from __future__ import annotations

import re
from typing import Optional

# ── Таблица сервисов ─────────────────────────────────────────────────────────
# (regex, tag, label, color_key)
# color_key соответствует ключам в constants.C
_SERVICES: list = [
    # Видео-стриминг
    (r"googlevideo\.com|youtu\.be|youtube\.com|ytimg\.com|ggpht\.com",
     "youtube",   "YouTube",    "err"),      # красный
    (r"nflxvideo\.net|nflxso\.net|netflix\.com|nflximg\.net",
     "netflix",   "Netflix",    "dim"),
    (r"ttvnw\.net|twitch\.tv|jtvnw\.net|twitch\.map\.fastly\.net",
     "twitch",    "Twitch",     "accent"),   # синий/фиолетовый
    (r"primevideo\.com|aiv-cdn\.net|amazonvideo\.com",
     "prime",     "PrimeVideo", "warn"),
    (r"dplus\.tv|disneyplus\.com|bamgrid\.com",
     "disney",    "Disney+",    "up"),
    # Соцсети / мессенджеры
    (r"cdninstagram\.com|instagram\.com|fbcdn\.net|facebook\.com|fb\.com|fbsbx\.com",
     "meta",      "Meta",       "warn"),     # оранжевый
    (r"tiktok\.com|tiktokcdn\.com|musical\.ly|tiktokv\.com",
     "tiktok",    "TikTok",     "accent2"),
    (r"t\.me|telegram\.org|telegram\.me|core\.telegram\.org|tdesktop\.com",
     "telegram",  "Telegram",   "up"),       # синий
    (r"twimg\.com|twitter\.com|x\.com|abs\.twimg\.com",
     "twitter",   "X/Twitter",  "dim"),
    (r"discord\.gg|discord\.com|discordapp\.com|discordmedia\.com",
     "discord",   "Discord",    "accent"),
    (r"vk\.com|vk-cdn\.net|userapi\.com|vkontakte\.ru",
     "vk",        "VK",         "up"),
    # Музыка
    (r"scdn\.co|spotifycdn\.com|spotify\.com|audio-sp-",
     "spotify",   "Spotify",    "ok"),       # зелёный
    (r"music\.yandex\.|yandexmusic\.",
     "yandex",    "Яндекс.Музыка", "warn"),
    # Игры
    (r"steamcontent\.com|steamgames\.com|steampowered\.com|valvesoftware\.com|akamaihd\.net.*steam",
     "steam",     "Steam",      "up"),
    (r"epicgames\.com|epiccdn\.com",
     "epic",      "Epic",       "dim"),
    (r"riotgames\.com|leagueoflegends\.com|valorant\.com",
     "riot",      "Riot",       "err"),
    # CDN / облако
    (r"cloudflare\.com|cloudflarestream\.com|cf-media\.stream",
     "cf",        "Cloudflare", "accent2"),
    (r"cloudfront\.net|amazonaws\.com|awsstatic\.com|aws\.com",
     "aws",       "AWS",        "warn"),
    (r"akamaihd\.net|akamaized\.net|edgekey\.net|edgesuite\.net",
     "akamai",    "Akamai",     "dim"),
    # Поиск / браузеры
    (r"gstatic\.com|googleapis\.com|google\.com|googleusercontent\.com",
     "google",    "Google",     "dim"),
    (r"apple\.com|icloud\.com|mzstatic\.com|applecdn\.net",
     "apple",     "Apple",      "dim"),
    (r"microsoft\.com|windowsupdate\.com|office\.com|microsoftonline\.com|live\.com",
     "microsoft", "Microsoft",  "up"),
    # ИИ
    (r"openai\.com|chatgpt\.com|oaistatic\.com",
     "openai",    "ChatGPT",    "ok"),
    (r"anthropic\.com|claude\.ai",
     "claude",    "Claude",     "ok"),
    # Торренты (tracker-домены)
    (r"torrent|tracker\.|announce\.",
     "torrent",   "Torrent",    "err"),
]

_compiled = [
    (re.compile(p, re.I), tag, label, color)
    for p, tag, label, color in _SERVICES
]


def classify(domain: str) -> Optional[tuple]:
    """Возвращает (tag, label, color_key) или None если домен не распознан."""
    if not domain:
        return None
    for rx, tag, label, color in _compiled:
        if rx.search(domain):
            return tag, label, color
    return None
