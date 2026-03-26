"""GeoIP: MaxMind GeoLite2 (.mmdb) с автоматическим фолбэком на ip-api.com.

Если установлен пакет ``maxminddb`` и найден файл GeoLite2-City.mmdb —
используется быстрый офлайн-поиск (без лимитов запросов).
Иначе запросы идут к ip-api.com (45 req/min, асинхронно в фоновом потоке).
"""

from __future__ import annotations

import json
import logging
import os
import time
import ipaddress
import threading
from collections import OrderedDict
from typing import Optional
from urllib.request import urlopen

log = logging.getLogger(__name__)

_CACHE_MAX = 2000
_CACHE_TTL_OFFLINE = 86400 * 7   # 7 дней для MaxMind (база обновляется редко)
_CACHE_TTL_ONLINE  = 3600        # 1 час для ip-api.com
_PENDING_TIMEOUT   = 30
_MAX_CONCURRENT    = 5

# Пути поиска mmdb-файлов
_MMDB_CITY_PATHS = [
    "/opt/xray-monitor/GeoLite2-City.mmdb",
    "/usr/share/GeoIP/GeoLite2-City.mmdb",
    "/var/lib/GeoIP/GeoLite2-City.mmdb",
    "/etc/GeoIP/GeoLite2-City.mmdb",
]
_MMDB_ASN_PATHS = [
    "/opt/xray-monitor/GeoLite2-ASN.mmdb",
    "/usr/share/GeoIP/GeoLite2-ASN.mmdb",
    "/var/lib/GeoIP/GeoLite2-ASN.mmdb",
    "/etc/GeoIP/GeoLite2-ASN.mmdb",
]

_FAIL = {"cc": "??", "country": "?", "city": "", "isp": "",
         "asn": "", "asname": "", "hosting": False}

try:
    import maxminddb as _mmdb_mod  # type: ignore[import-untyped]
    _HAS_MAXMIND = True
except ImportError:
    _mmdb_mod = None  # type: ignore[assignment]
    _HAS_MAXMIND = False


def _flag(cc: str) -> str:
    return ""  # emoji флаги не поддерживаются в большинстве SSH-терминалов


class GeoIP:
    def __init__(self) -> None:
        self._cache: OrderedDict = OrderedDict()   # ip -> (ts, data)
        self._pending: dict = {}                   # ip -> ts (только для API)
        self._lock = threading.Lock()
        self._semaphore = threading.Semaphore(_MAX_CONCURRENT)
        self._mmdb_city = None
        self._mmdb_asn  = None
        self._offline   = False     # True если MaxMind загружен успешно
        self._init_maxmind()

    # ── Инициализация MaxMind ─────────────────────────────────

    def _init_maxmind(self) -> None:
        if not _HAS_MAXMIND or _mmdb_mod is None:
            return
        for path in _MMDB_CITY_PATHS:
            if os.path.exists(path):
                try:
                    self._mmdb_city = _mmdb_mod.open_database(path)
                    self._offline   = True
                    break
                except Exception:
                    pass
        for path in _MMDB_ASN_PATHS:
            if os.path.exists(path):
                try:
                    self._mmdb_asn = _mmdb_mod.open_database(path)
                    break
                except Exception:
                    pass

    @property
    def backend(self) -> str:
        if self._offline:
            asn_note = "+ASN" if self._mmdb_asn else ""
            return f"MaxMind{asn_note}"
        return "ip-api.com"

    # ── Поиск через MaxMind (синхронный, локальный файл) ──────

    def _lookup_maxmind(self, ip: str) -> Optional[dict]:
        if not self._mmdb_city:
            return None
        try:
            record = self._mmdb_city.get(ip)
            if record is None:
                return dict(_FAIL)
            country = record.get("country") or {}
            city_r  = record.get("city") or {}
            cc      = country.get("iso_code", "")
            cname   = (country.get("names") or {}).get("en", "")
            city_n  = (city_r.get("names") or {}).get("en", "")

            asn = asname = ""
            if self._mmdb_asn:
                try:
                    ar = self._mmdb_asn.get(ip)
                    if ar:
                        num    = ar.get("autonomous_system_number", "")
                        org    = ar.get("autonomous_system_organization", "")
                        asn    = f"AS{num}" if num else ""
                        asname = org or ""
                except Exception:
                    pass

            return {
                "cc":      cc,
                "country": cname,
                "city":    city_n,
                "isp":     asname,
                "asn":     asn,
                "asname":  asname,
                "hosting": False,   # MaxMind Free не содержит hosting-флага
            }
        except Exception:
            return None

    # ── Кэш ──────────────────────────────────────────────────

    def _cache_set(self, ip: str, data: dict) -> None:
        with self._lock:
            while len(self._cache) >= _CACHE_MAX:
                self._cache.popitem(last=False)
            self._cache[ip] = (time.monotonic(), data)
            self._pending.pop(ip, None)

    # ── Публичный lookup ──────────────────────────────────────

    def lookup(self, ip: str) -> Optional[dict]:
        clean = ip.strip("[]")
        try:
            a = ipaddress.ip_address(clean)
            if a.is_private or a.is_loopback:
                return {"cc": "LO", "country": "Local", "city": "", "isp": "",
                        "asn": "", "asname": "", "hosting": False}
        except (ValueError, TypeError):
            pass

        ttl = _CACHE_TTL_OFFLINE if self._offline else _CACHE_TTL_ONLINE
        now = time.monotonic()

        with self._lock:
            if clean in self._cache:
                ts, data = self._cache[clean]
                if now - ts < ttl:
                    self._cache.move_to_end(clean)
                    return data
                del self._cache[clean]

        # MaxMind: синхронный поиск (локальный файл — быстро, не блокирует UI)
        if self._offline:
            result = self._lookup_maxmind(clean)
            if result is not None:
                self._cache_set(clean, result)
                return result
            # Файл есть, но IP не найден — вернём FAIL немедленно

        # ip-api.com: запускаем фоновый поток, пока возвращаем None
        with self._lock:
            if clean in self._pending:
                if now - self._pending[clean] < _PENDING_TIMEOUT:
                    return None
                del self._pending[clean]
            self._pending[clean] = now

        # Semaphore проверяется до создания потока, чтобы избежать
        # неограниченного создания daemon-потоков
        if not self._semaphore.acquire(blocking=False):
            return None
        threading.Thread(target=self._fetch_api, args=(clean,), daemon=True).start()
        return None

    def _fetch_api(self, ip: str) -> None:
        # Semaphore уже захвачен вызывающим кодом
        try:
            # HTTPS для защиты от MITM (ip-api.com поддерживает на платных планах;
            # бесплатный лимит — HTTP. Пробуем HTTPS, фолбэк на HTTP.)
            for scheme in ("https", "http"):
                try:
                    raw = urlopen(
                        f"{scheme}://ip-api.com/json/{ip}"
                        "?fields=status,country,countryCode,city,isp,as,asname,hosting",
                        timeout=5,
                    ).read()
                    break
                except Exception:
                    if scheme == "http":
                        raise
                    continue  # fallback to http
            r = json.loads(raw)
            if r.get("status") == "success":
                res: dict = {
                    "cc":      r.get("countryCode", ""),
                    "country": r.get("country", ""),
                    "city":    r.get("city", ""),
                    "isp":     r.get("isp", ""),
                    "asn":     r.get("as", ""),
                    "asname":  r.get("asname", ""),
                    "hosting": r.get("hosting", False),
                }
            else:
                res = dict(_FAIL)
        except Exception:
            log.debug("GeoIP API error for %s", ip, exc_info=True)
            res = dict(_FAIL)
        finally:
            self._semaphore.release()
        self._cache_set(ip, res)

    # ── Форматирование ────────────────────────────────────────

    def fmt(self, ip: str) -> str:
        info = self.lookup(ip)
        if not info: return "..."
        cc     = info.get("cc", "??")
        city   = info.get("city", "")
        asname = info.get("asname", "")
        detail = city[:14] if city else asname[:14]
        return f"{cc}" + (f" {detail}" if detail else "")

    def fmt_full(self, ip: str) -> tuple:
        info = self.lookup(ip)
        if not info: return "...", "", False
        cc      = info.get("cc", "??")
        city    = info.get("city", "")
        asn     = info.get("asn", "")
        asname  = info.get("asname", "")
        hosting = info.get("hosting", False)
        asn_num  = asn.split()[0] if asn else ""
        asn_name = asname[:22] if asname else (asn[len(asn_num):].strip()[:22] if asn_num else "")
        # Если нет города — показываем провайдера как подсказку
        detail  = city[:12] if city else asname[:12]
        geo_str = f"{cc}" + (f" {detail}" if detail else "")
        asn_str = f"{asn_num} {asn_name}".strip()
        return geo_str, asn_str, hosting
