"""GeoIP поиск через ip-api.com с TTL-кэшем (LRU)."""

from __future__ import annotations

import json
import time
import ipaddress
import threading
from collections import OrderedDict
from typing import Optional
from urllib.request import urlopen

_CACHE_MAX = 1500
_CACHE_TTL = 3600          # 1 час
_PENDING_TIMEOUT = 30      # секунд до повторной попытки
_MAX_CONCURRENT = 5        # макс. параллельных запросов

_FAIL = {"cc": "??", "country": "?", "city": "", "isp": "",
         "asn": "", "asname": "", "hosting": False}


def _flag(cc: str) -> str:
    if not cc or len(cc) != 2: return ""
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in cc.upper())


class GeoIP:
    def __init__(self) -> None:
        self._cache: OrderedDict = OrderedDict()   # ip -> (timestamp, data)
        self._pending: dict = {}                   # ip -> timestamp
        self._lock = threading.Lock()
        self._semaphore = threading.Semaphore(_MAX_CONCURRENT)

    def lookup(self, ip: str) -> Optional[dict]:
        clean = ip.strip("[]")
        try:
            a = ipaddress.ip_address(clean)
            if a.is_private or a.is_loopback:
                return {"cc": "LO", "country": "Local", "city": "", "isp": "",
                        "asn": "", "asname": "", "hosting": False}
        except (ValueError, TypeError):
            pass

        now = time.monotonic()
        with self._lock:
            if clean in self._cache:
                ts, data = self._cache[clean]
                if now - ts < _CACHE_TTL:
                    self._cache.move_to_end(clean)
                    return data
                del self._cache[clean]

            if clean in self._pending:
                if now - self._pending[clean] < _PENDING_TIMEOUT:
                    return None
                del self._pending[clean]

            self._pending[clean] = now

        threading.Thread(target=self._fetch, args=(clean,), daemon=True).start()
        return None

    def _fetch(self, ip: str) -> None:
        if not self._semaphore.acquire(timeout=5):
            with self._lock:
                self._pending.pop(ip, None)
            return

        try:
            raw = urlopen(
                f"http://ip-api.com/json/{ip}?fields=status,country,countryCode,city,isp,as,asname,hosting",
                timeout=5).read()
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
            res = dict(_FAIL)
        finally:
            self._semaphore.release()

        now = time.monotonic()
        with self._lock:
            while len(self._cache) >= _CACHE_MAX:
                self._cache.popitem(last=False)
            self._cache[ip] = (now, res)
            self._pending.pop(ip, None)

    def fmt(self, ip: str) -> str:
        info = self.lookup(ip)
        if not info: return "..."
        cc   = info.get("cc", "??")
        city = info.get("city", "")
        return f"{_flag(cc)} {cc}" + (f" {city[:14]}" if city else "")

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
        geo_str  = f"{_flag(cc)} {cc}" + (f" {city[:12]}" if city else "")
        asn_str  = f"{asn_num} {asn_name}".strip()
        return geo_str, asn_str, hosting
