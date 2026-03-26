"""Единый источник истины для IP-состояния.

IPRegistry объединяет данные из:
  - gRPC (ip_bytes, online status)
  - access.log (client_ips, SNI)
  - SQLite (ip_traffic, ip_sni)

Thread safety: один Lock, все read/write через него.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .traffic_log import TrafficLog

log = logging.getLogger(__name__)

_MAX_RECORDS = 50_000       # макс. IP в памяти
_PRUNE_AGE   = 30 * 86400   # 30 дней — удаляем неактивные записи
_PRUNE_EVERY = 100           # проверка каждые N вызовов flush_to_db


@dataclass(slots=True)
class IPRecord:
    ip: str
    email: str = ""
    up: float = 0.0
    dn: float = 0.0
    first_seen: float = 0.0
    last_active: float = 0.0
    sni: deque = field(default_factory=lambda: deque(maxlen=50))


class IPRegistry:
    def __init__(self, traffic_log: TrafficLog) -> None:
        self._tl = traffic_log
        self._lock = threading.Lock()
        self._records: dict[str, IPRecord] = {}
        self._online: set[str] = set()
        # client_ips staging: {email: {ip: ts}} — для backward compat
        self._client_ips: dict[str, dict[str, float]] = {}
        # SNI flush buffer: {ip: {domain: (tag, count, last_ts)}}
        self._sni_flush: dict = {}
        # Dirty tracking for flush_to_db
        self._dirty_ips: set[str] = set()
        self._flush_n: int = 0

    # ── Write API (worker threads) ────────────────────────────

    def update_traffic(self, ip: str, email: str,
                       delta_up: float, delta_dn: float) -> None:
        """Вызывается из stats.fetch() вместо записи в ip_bytes/ip_email."""
        with self._lock:
            rec = self._records.get(ip)
            if rec is None:
                rec = IPRecord(ip=ip, email=email)
                self._records[ip] = rec
            rec.up += delta_up
            rec.dn += delta_dn
            if email:
                rec.email = email
            rec.last_active = time.time()
            self._dirty_ips.add(ip)

    def update_connections(self, client_ips: dict[str, dict[str, float]]) -> None:
        """Вызывается из app._tick_worker после log_snap."""
        now = time.time()
        cutoff = now - 86400
        with self._lock:
            self._client_ips = client_ips
            for email, ips in client_ips.items():
                for ip, ts in ips.items():
                    if ts < cutoff:
                        continue
                    rec = self._records.get(ip)
                    if rec is None:
                        rec = IPRecord(ip=ip, email=email,
                                       first_seen=ts, last_active=ts)
                        self._records[ip] = rec
                    else:
                        if email:
                            rec.email = email
                        if rec.first_seen == 0 or ts < rec.first_seen:
                            rec.first_seen = ts
                        if ts > rec.last_active:
                            rec.last_active = ts

    def update_online(self, online_ips: set[str]) -> None:
        """Вызывается из stats.fetch() после _track()."""
        with self._lock:
            self._online = set(online_ips)

    def update_sni(self, sni_buf: dict) -> None:
        """Вызывается из app._tick_worker.

        sni_buf: {ip: {domain: (tag, count, last_ts)}}
        """
        from .sni_radar import classify as _classify
        with self._lock:
            for ip, domains in sni_buf.items():
                rec = self._records.get(ip)
                if rec is None:
                    rec = IPRecord(ip=ip)
                    self._records[ip] = rec
                for domain, (tag, cnt, ts_d) in domains.items():
                    rec.sni.append((domain, ts_d))
                    # Accumulate in flush buffer for DB write
                    if ip not in self._sni_flush:
                        self._sni_flush[ip] = {}
                    cls = _classify(domain)
                    db_tag = cls[0] if cls else (tag or "")
                    ex = self._sni_flush[ip].get(domain)
                    if ex is None:
                        self._sni_flush[ip][domain] = (db_tag, cnt, ts_d)
                    else:
                        self._sni_flush[ip][domain] = (
                            db_tag or ex[0], ex[1] + cnt, max(ex[2], ts_d)
                        )

    # ── Read API (UI thread) ──────────────────────────────────

    def get_online_ips(self) -> set[str]:
        with self._lock:
            return set(self._online)

    def get_record(self, ip: str) -> IPRecord | None:
        with self._lock:
            return self._records.get(ip)

    def get_all_records(self) -> dict[str, IPRecord]:
        with self._lock:
            return dict(self._records)

    def get_ip_bytes(self, ip: str) -> tuple[float, float]:
        with self._lock:
            rec = self._records.get(ip)
            if rec is None:
                return (0.0, 0.0)
            return (rec.up, rec.dn)

    def get_email_for_ip(self, ip: str) -> str:
        with self._lock:
            rec = self._records.get(ip)
            return rec.email if rec else ""

    def get_client_ips(self) -> dict[str, dict[str, float]]:
        """Backward compat: возвращает {email: {ip: ts}}."""
        with self._lock:
            return {em: dict(ips) for em, ips in self._client_ips.items()}

    def get_ip_sni(self, ip: str) -> deque | None:
        with self._lock:
            rec = self._records.get(ip)
            if rec is None or not rec.sni:
                return None
            return deque(rec.sni)

    def get_total_count(self) -> int:
        with self._lock:
            return len(self._records)

    # ── Delete ────────────────────────────────────────────────

    def delete_ip(self, ip: str) -> None:
        """Единая точка удаления IP (память + DB)."""
        with self._lock:
            self._records.pop(ip, None)
            self._online.discard(ip)
            self._dirty_ips.discard(ip)
            self._sni_flush.pop(ip, None)
            for em_ips in self._client_ips.values():
                em_ips.pop(ip, None)
        self._tl.delete_by_ip(ip)

    # ── Eviction ──────────────────────────────────────────────

    def _prune_stale(self) -> None:
        """Удаляет неактивные IP из памяти (под self._lock)."""
        now = time.time()
        cutoff = now - _PRUNE_AGE
        to_remove = []
        for ip, rec in self._records.items():
            if ip in self._online:
                continue
            if rec.last_active > 0 and rec.last_active < cutoff:
                to_remove.append(ip)
        # Если всё ещё превышаем лимит — удаляем самые старые
        if len(self._records) - len(to_remove) > _MAX_RECORDS:
            by_age = sorted(
                ((ip, rec.last_active) for ip, rec in self._records.items()
                 if ip not in self._online and ip not in to_remove),
                key=lambda x: x[1],
            )
            excess = len(self._records) - len(to_remove) - _MAX_RECORDS
            to_remove.extend(ip for ip, _ in by_age[:excess])
        for ip in to_remove:
            self._records.pop(ip, None)
            self._dirty_ips.discard(ip)
            self._sni_flush.pop(ip, None)
        if to_remove:
            log.debug("pruned %d stale IP records", len(to_remove))

    # ── Persistence ───────────────────────────────────────────

    def flush_to_db(self) -> None:
        """Снимок под lock → DB write без lock."""
        with self._lock:
            sni_buf = self._sni_flush
            self._sni_flush = {}
            dirty = self._dirty_ips
            # НЕ очищаем dirty до успешной записи
            ip_bytes_snap: dict = {}
            email_snap: dict = {}
            for ip in dirty:
                rec = self._records.get(ip)
                if rec:
                    ip_bytes_snap[ip] = [rec.up, rec.dn]
                    email_snap[ip] = rec.email
            conn_snap = {em: dict(ips) for em, ips in self._client_ips.items()}

        # DB writes without lock
        ok = True
        try:
            if sni_buf:
                self._tl.save_ip_sni(sni_buf)
            if ip_bytes_snap:
                self._tl.save_ip_bytes(ip_bytes_snap, email_snap)
            if conn_snap:
                self._tl.save_ip_connections(conn_snap)
        except Exception:
            ok = False
            log.warning("flush_to_db failed", exc_info=True)

        # Очищаем dirty только после успешной записи
        if ok:
            with self._lock:
                self._dirty_ips -= dirty  # убираем только те, что записали

        # Периодическая очистка устаревших записей
        self._flush_n += 1
        if self._flush_n % _PRUNE_EVERY == 0:
            with self._lock:
                self._prune_stale()

    def load_from_db(self) -> None:
        """Загрузка при старте из TrafficLog."""
        stored_bytes = self._tl.load_ip_bytes()
        stored_sni = self._tl.load_ip_sni()
        all_ips = self._tl.query_all_ips()

        with self._lock:
            for row in all_ips:
                ip = row["ip"]
                rec = self._records.get(ip)
                if rec is None:
                    rec = IPRecord(ip=ip)
                    self._records[ip] = rec
                rec.email = row.get("email", "") or rec.email
                rec.up = max(rec.up, row.get("up", 0))
                rec.dn = max(rec.dn, row.get("dn", 0))
                fs = row.get("first_seen", 0)
                if fs and (rec.first_seen == 0 or fs < rec.first_seen):
                    rec.first_seen = fs
                la = row.get("last_active", 0)
                if la > rec.last_active:
                    rec.last_active = la

            for ip, vals in stored_bytes.items():
                rec = self._records.get(ip)
                if rec is None:
                    rec = IPRecord(ip=ip)
                    self._records[ip] = rec
                rec.up = max(rec.up, vals[0])
                rec.dn = max(rec.dn, vals[1])

            for ip, entries in stored_sni.items():
                rec = self._records.get(ip)
                if rec is None:
                    rec = IPRecord(ip=ip)
                    self._records[ip] = rec
                for domain, _tag, _hits, last_seen in entries:
                    rec.sni.append((domain, last_seen))
