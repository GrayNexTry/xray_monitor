"""Мониторинг лога доступа Xray и статистики блокировок."""

from __future__ import annotations

import logging
import os
import re
import time
import threading
from collections import deque, OrderedDict
from datetime import datetime

log = logging.getLogger(__name__)

_TOP_BLOCKED_MAX = 500
_RE_TRANSPORT    = re.compile(r"(?:tcp|udp):([^:,\s\[]+):(\d+)")
_RE_IPV4         = re.compile(r"^\d+\.\d+\.\d+\.\d+$")

# Парсинг клиентских подключений из access.log
# Формат: "2026/01/01 12:00:00 1.2.3.4:54321 accepted tcp:... email: user@tag"
# или IPv6: "[::1]:54321 accepted ..."
_RE_LOG_TS  = re.compile(r"^(\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2})")
_RE_SRC_IP  = re.compile(
    r"(?:^|\s)(\d{1,3}(?:\.\d{1,3}){3}):\d+\s+accepted"
    r"|(?:^|\s)\[([0-9a-fA-F:]+)\]:\d+\s+accepted"
)
_RE_EMAIL   = re.compile(r"email:\s*(\S+)")

# SNI/dest из строк с включённым sniffing:
# "... email: user@tag -> rr1.googlevideo.com:443 tls:..."
# или "... dest: googlevideo.com:443 ..."
_RE_SNI     = re.compile(
    r"(?:->|dest:)\s*([a-zA-Z][a-zA-Z0-9._-]{2,}\.[a-zA-Z]{2,}):\d+"
)


def _parse_log_ts(line: str, fallback: float) -> float:
    """Парсит timestamp из строки лога вида '2026/01/01 12:00:00 ...'."""
    m = _RE_LOG_TS.match(line)
    if not m:
        return fallback
    try:
        return datetime.strptime(m.group(1), "%Y/%m/%d %H:%M:%S").timestamp()
    except ValueError:
        return fallback


class LogTail:
    def __init__(self, path: str = "/var/log/xray/access.log", n: int = 80) -> None:
        self.path   = path
        self.n      = n
        self._lock  = threading.Lock()
        self._block_total:   int  = 0
        self._block_session: int  = 0
        self._block_window: deque = deque(maxlen=600)
        self._last_pos:      int  = 0
        self._last_size:     int  = 0
        self._last_inode:    int  = 0
        self._top_blocked: OrderedDict = OrderedDict()
        # IP-адреса клиентов из access.log: email -> {ip: last_seen_ts}
        self.client_ips: dict = {}
        # Буфер новых хитов для записи в БД: ip -> {domain: (tag, count, last_ts)}
        self._sni_flush: dict = {}

    def read(self) -> list:
        try:
            if not os.path.exists(self.path): return []
            with open(self.path, "rb") as f:
                f.seek(0, 2)
                sz = f.tell()
                f.seek(max(0, sz - 65536))
                return f.read().decode("utf-8", errors="replace").strip().split("\n")[-self.n:]
        except Exception:
            log.debug("failed to read log %s", self.path, exc_info=True)
            return []

    def update_block_stats(self) -> None:
        try:
            if not os.path.exists(self.path): return

            # Детект ротации лога: по inode (Linux) или по уменьшению размера (Windows)
            try:
                st = os.stat(self.path)
                current_inode = st.st_ino
                # На Windows st_ino == 0, поэтому используем только проверку размера
                if current_inode != 0 and self._last_inode != 0 \
                        and current_inode != self._last_inode:
                    self._last_pos = 0
                self._last_inode = current_inode
            except (AttributeError, OSError):
                pass

            with open(self.path, "rb") as f:
                f.seek(0, 2)
                sz = f.tell()
                if sz < self._last_size:
                    # Файл стал меньше — ротация (работает на всех платформах)
                    self._last_pos = 0
                self._last_size = sz
                is_first_scan = (self._last_pos == 0)
                if is_first_scan:
                    f.seek(max(0, sz - 2 * 1024 * 1024))  # 2 МБ — достаточно для начала
                else:
                    f.seek(self._last_pos)
                chunk = f.read().decode("utf-8", errors="replace")
                self._last_pos = f.tell()
                if is_first_scan:
                    self._block_total   = 0
                    self._block_session = 0
                    self._block_window.clear()
                    self._top_blocked.clear()

            now = time.time()
            block_count = 0
            new_client_ips: dict = {}
            new_sni: dict = {}  # ip -> {domain: (tag, count, last_ts)}
            for line in chunk.splitlines():
                ll = line.lower()

                # ── Парсим IP клиента из строк "accepted" ────────
                if "accepted" in ll:
                    m_ip    = _RE_SRC_IP.search(line)
                    m_email = _RE_EMAIL.search(line)
                    if m_ip and m_email:
                        ip    = m_ip.group(1) or m_ip.group(2) or ""
                        email = m_email.group(1).strip()
                        if ip and email:
                            ts_line = _parse_log_ts(line, now)
                            if email not in new_client_ips:
                                new_client_ips[email] = {}
                            # Берём наибольший timestamp для этого IP
                            prev_ts = new_client_ips[email].get(ip, 0)
                            new_client_ips[email][ip] = max(prev_ts, ts_line)

                    # ── SNI Radar: парсим dest-домен ──────────────
                    # Только если домен есть (sniffing включён в xray)
                    m_sni = _RE_SNI.search(line)
                    if m_sni and m_ip:
                        domain = m_sni.group(1).lower()
                        ip_sni = m_ip.group(1) or m_ip.group(2) or ""
                        if ip_sni and domain and not domain.replace(".", "").isdigit():
                            ts_line = _parse_log_ts(line, now)
                            # Обновляем кольцевой буфер
                            if ip_sni not in new_sni:
                                new_sni[ip_sni] = {}
                            entry = new_sni[ip_sni].get(domain)
                            if entry is None:
                                new_sni[ip_sni][domain] = (None, 1, ts_line)
                            else:
                                new_sni[ip_sni][domain] = (
                                    entry[0], entry[1] + 1, max(entry[2], ts_line)
                                )

                if "-> block" not in ll and "->block" not in ll:
                    continue
                block_count += 1
                after_accepted = line
                acc_idx = ll.find(" accepted ")
                if acc_idx >= 0:
                    after_accepted = line[acc_idx + 10:]
                m = _RE_TRANSPORT.search(after_accepted)
                if not m:
                    continue
                target = m.group(1).lower()
                port   = m.group(2)
                is_ip  = bool(_RE_IPV4.match(target))
                if is_ip:
                    if (target.startswith("224.") or target.startswith("239.")
                            or target == "255.255.255.255"):
                        continue
                    key = f"[ip]  {target}:{port}"
                else:
                    key = target
                self._top_blocked[key] = self._top_blocked.get(key, 0) + 1

            while len(self._top_blocked) > _TOP_BLOCKED_MAX:
                self._top_blocked.popitem(last=False)

            with self._lock:
                self._block_total   += block_count
                self._block_session += block_count
                self._block_window.extend([now] * block_count)
                # Обновляем client_ips, сохраняем максимальный ts, убираем старше 24 ч
                for email, ips in new_client_ips.items():
                    if email not in self.client_ips:
                        self.client_ips[email] = {}
                    for ip, ts_val in ips.items():
                        prev = self.client_ips[email].get(ip, 0)
                        self.client_ips[email][ip] = max(prev, ts_val)
                cutoff = now - 86400
                for email in list(self.client_ips):
                    self.client_ips[email] = {
                        ip: ts for ip, ts in self.client_ips[email].items()
                        if ts > cutoff
                    }
                    if not self.client_ips[email]:
                        del self.client_ips[email]

                # ── Обновляем SNI flush-буфер (для передачи в IPRegistry) ──
                from .sni_radar import classify as _classify
                for ip_sni_k, domains in new_sni.items():
                    if ip_sni_k not in self._sni_flush:
                        self._sni_flush[ip_sni_k] = {}
                    for domain, (_, cnt, ts_d) in domains.items():
                        cls = _classify(domain)
                        tag = cls[0] if cls else ""
                        ex  = self._sni_flush[ip_sni_k].get(domain)
                        if ex is None:
                            self._sni_flush[ip_sni_k][domain] = (tag, cnt, ts_d)
                        else:
                            self._sni_flush[ip_sni_k][domain] = (
                                tag or ex[0], ex[1] + cnt, max(ex[2], ts_d)
                            )
        except Exception:
            log.debug("update_block_stats failed", exc_info=True)

    def block_per_min(self) -> float:
        with self._lock:
            if not self._block_window: return 0.0
            now    = time.time()
            cutoff = now - 300
            count  = 0
            oldest = now
            for t in self._block_window:
                if t > cutoff:
                    count += 1
                    if t < oldest:
                        oldest = t
            if count == 0: return 0.0
            elapsed = now - oldest
            return count / max(elapsed / 60.0, 0.017)

    def top_blocked(self, n: int = 5) -> list:
        with self._lock:
            return sorted(self._top_blocked.items(),
                          key=lambda x: x[1], reverse=True)[:n]

    def flush_new_sni(self) -> dict:
        """Возвращает накопленные SNI-хиты и очищает буфер (для записи в БД)."""
        with self._lock:
            buf = self._sni_flush
            self._sni_flush = {}
        return buf

