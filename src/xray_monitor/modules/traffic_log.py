"""Персистентный учёт трафика через SQLite.

Схема БД:
  daily_traffic(date, email, up, dn)   — итоги за каждый день
  today_base(email, up_abs, dn_abs, pre_up, pre_dn) — база текущего дня
  meta(key, value)                     — today_date и прочие метаданные

Автоматически мигрирует данные из старого traffic_history.json при первом запуске.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from datetime import date, timedelta
from typing import Dict

_LOCK      = threading.Lock()
_SAVE_EVERY = 30    # сохранять today_base каждые N тиков

_DEFAULT_PATH = os.environ.get(
    "XRAY_MONITOR_DATA",
    "/opt/xray-monitor/traffic_history.db",
)

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS daily_traffic (
    date  TEXT NOT NULL,
    email TEXT NOT NULL,
    up    INTEGER NOT NULL DEFAULT 0,
    dn    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (date, email)
);
CREATE TABLE IF NOT EXISTS today_base (
    email  TEXT    NOT NULL PRIMARY KEY,
    up_abs INTEGER NOT NULL DEFAULT 0,
    dn_abs INTEGER NOT NULL DEFAULT 0,
    pre_up INTEGER NOT NULL DEFAULT 0,
    pre_dn INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT NOT NULL PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);

-- SNI Radar: накопленный трафик по IP-адресам клиентов
CREATE TABLE IF NOT EXISTS ip_traffic (
    ip          TEXT    NOT NULL PRIMARY KEY,
    email       TEXT    NOT NULL DEFAULT '',
    up          INTEGER NOT NULL DEFAULT 0,
    dn          INTEGER NOT NULL DEFAULT 0,
    updated     INTEGER NOT NULL DEFAULT 0,
    first_seen  INTEGER NOT NULL DEFAULT 0,
    last_active INTEGER NOT NULL DEFAULT 0
);

-- SNI Radar: домены/сервисы которые посещал каждый IP
CREATE TABLE IF NOT EXISTS ip_sni (
    ip        TEXT    NOT NULL,
    domain    TEXT    NOT NULL,
    tag       TEXT    NOT NULL DEFAULT '',
    hits      INTEGER NOT NULL DEFAULT 0,
    last_seen INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (ip, domain)
);
CREATE INDEX IF NOT EXISTS idx_ip_sni_ip ON ip_sni(ip);
"""


class TrafficLog:
    """Сохраняет ежедневные снэпшоты трафика xray и считает периоды."""

    def __init__(self, path: str = _DEFAULT_PATH) -> None:
        self.path        = path
        self._last_abs:  dict = {}   # последние абс. значения от gRPC (только в памяти)
        self._today_date: str = ""
        self._today_base: dict = {}  # email -> {up_abs, dn_abs, pre_up, pre_dn}
        self._tick_n     = 0
        self._conn       = self._open_db()
        self._load_today()
        self._maybe_migrate_json()

    # ── Инициализация БД ─────────────────────────────────────

    def _open_db(self) -> sqlite3.Connection:
        d = os.path.dirname(self.path)
        if d:
            os.makedirs(d, exist_ok=True)
        conn = sqlite3.connect(self.path, check_same_thread=False, timeout=10)
        conn.executescript(_SCHEMA)
        conn.commit()
        self._migrate_schema(conn)
        return conn

    @staticmethod
    def _migrate_schema(conn: sqlite3.Connection) -> None:
        """Добавляет новые колонки к существующим таблицам (безопасная миграция)."""
        cols = {r[1] for r in conn.execute("PRAGMA table_info(ip_traffic)").fetchall()}
        migrations = []
        if "first_seen" not in cols:
            migrations.append(
                "ALTER TABLE ip_traffic ADD COLUMN first_seen INTEGER NOT NULL DEFAULT 0"
            )
        if "last_active" not in cols:
            migrations.append(
                "ALTER TABLE ip_traffic ADD COLUMN last_active INTEGER NOT NULL DEFAULT 0"
            )
        for sql in migrations:
            try:
                conn.execute(sql)
            except Exception:
                pass
        if migrations:
            conn.commit()

    def _load_today(self) -> None:
        try:
            with _LOCK:
                row = self._conn.execute(
                    "SELECT value FROM meta WHERE key='today_date'"
                ).fetchone()
                self._today_date = row[0] if row else ""
                rows = self._conn.execute(
                    "SELECT email, up_abs, dn_abs, pre_up, pre_dn FROM today_base"
                ).fetchall()
                self._today_base = {
                    r[0]: {
                        "up_abs": r[1], "dn_abs": r[2],
                        "pre_up": r[3], "pre_dn": r[4],
                    }
                    for r in rows
                }
        except Exception:
            self._today_date = ""
            self._today_base = {}

    # ── Миграция из JSON ─────────────────────────────────────

    def _maybe_migrate_json(self) -> None:
        json_path = self.path.replace(".db", ".json") if self.path.endswith(".db") \
                    else self.path + ".bak.json"
        if not os.path.exists(json_path):
            return
        try:
            import json
            with open(json_path) as f:
                old = json.load(f)
            days = old.get("days", {})
            with _LOCK:
                with self._conn:
                    for date_str, users in days.items():
                        for email, v in users.items():
                            self._conn.execute(
                                "INSERT OR IGNORE INTO daily_traffic(date,email,up,dn)"
                                " VALUES(?,?,?,?)",
                                (date_str, email, v.get("up", 0), v.get("dn", 0)),
                            )
            os.rename(json_path, json_path + ".migrated")
        except Exception:
            pass

    # ── Сохранение today_base ────────────────────────────────

    def _save_today_base(self) -> None:
        """Записывает текущую базу дня в SQLite. Вызывается под _LOCK."""
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES('today_date', ?)",
                (self._today_date,),
            )
            self._conn.execute("DELETE FROM today_base")
            rows = [
                (em, b["up_abs"], b["dn_abs"], b["pre_up"], b["pre_dn"])
                for em, b in self._today_base.items()
            ]
            self._conn.executemany(
                "INSERT INTO today_base(email, up_abs, dn_abs, pre_up, pre_dn)"
                " VALUES(?,?,?,?,?)",
                rows,
            )

    # ── Вычисление дельты ────────────────────────────────────

    def _compute_today(self, users_abs: dict) -> dict:
        """Трафик за сегодня = pre + (current_abs - base_abs) для каждого email."""
        result: dict = {}
        for email, vals in users_abs.items():
            b = self._today_base.get(
                email,
                {"up_abs": 0, "dn_abs": 0, "pre_up": 0, "pre_dn": 0},
            )
            d_up = max(0, vals.get("uplink",   0) - b["up_abs"])
            d_dn = max(0, vals.get("downlink", 0) - b["dn_abs"])
            result[email] = {
                "up": b["pre_up"] + d_up,
                "dn": b["pre_dn"] + d_dn,
            }
        return result

    # ── Публичный API ────────────────────────────────────────

    def update(self, users_abs: dict) -> None:
        """Вызывается каждый тик.
        users_abs: {"email@tag": {"uplink": int, "downlink": int}} — абс. счётчики gRPC.
        """
        if not users_abs:
            return
        today = date.today().isoformat()

        with _LOCK:
            # ── Новый день ────────────────────────────────────
            if self._today_date != today:
                if self._today_date and self._last_abs:
                    # Финализируем вчера
                    prev_deltas = self._compute_today(self._last_abs)
                    with self._conn:
                        for em, v in prev_deltas.items():
                            self._conn.execute(
                                "INSERT INTO daily_traffic(date,email,up,dn) VALUES(?,?,?,?)"
                                " ON CONFLICT(date,email) DO UPDATE SET"
                                " up=excluded.up, dn=excluded.dn",
                                (self._today_date, em, v["up"], v["dn"]),
                            )
                        # Ротация: храним не более 90 дней
                        cutoff = (date.today() - timedelta(days=90)).isoformat()
                        self._conn.execute(
                            "DELETE FROM daily_traffic WHERE date < ?", (cutoff,)
                        )
                # Инициализируем новый день
                self._today_date = today
                self._today_base = {
                    em: {
                        "up_abs": v.get("uplink",   0),
                        "dn_abs": v.get("downlink", 0),
                        "pre_up": 0,
                        "pre_dn": 0,
                    }
                    for em, v in users_abs.items()
                }
                self._last_abs = {em: dict(v) for em, v in users_abs.items()}
                self._save_today_base()
                return

            # ── Детект рестарта xray (счётчики упали) ────────
            if self._last_abs and self._today_base:
                restarted = any(
                    (
                        users_abs.get(em, {}).get("downlink", 0) <
                        self._last_abs.get(em, {}).get("downlink", 0)
                        or
                        users_abs.get(em, {}).get("uplink", 0) <
                        self._last_abs.get(em, {}).get("uplink", 0)
                    )
                    for em in self._today_base
                    if self._today_base.get(em, {}).get("dn_abs", 0) > 102_400
                )
                if restarted:
                    # Накапливаем дельту до рестарта в pre
                    for em, vals in self._last_abs.items():
                        if em not in self._today_base:
                            continue
                        b    = self._today_base[em]
                        d_up = max(0, vals.get("uplink",   0) - b["up_abs"])
                        d_dn = max(0, vals.get("downlink", 0) - b["dn_abs"])
                        b["pre_up"] += d_up
                        b["pre_dn"] += d_dn
                    # Новый baseline после рестарта
                    for em, vals in users_abs.items():
                        if em in self._today_base:
                            self._today_base[em]["up_abs"] = vals.get("uplink",   0)
                            self._today_base[em]["dn_abs"] = vals.get("downlink", 0)
                        else:
                            self._today_base[em] = {
                                "up_abs": vals.get("uplink",   0),
                                "dn_abs": vals.get("downlink", 0),
                                "pre_up": 0,
                                "pre_dn": 0,
                            }

            # ── Записываем сегодня ────────────────────────────
            deltas = self._compute_today(users_abs)
            with self._conn:
                self._conn.executemany(
                    "INSERT INTO daily_traffic(date,email,up,dn) VALUES(?,?,?,?)"
                    " ON CONFLICT(date,email) DO UPDATE SET"
                    " up=excluded.up, dn=excluded.dn",
                    [(today, em, v["up"], v["dn"]) for em, v in deltas.items()],
                )

            self._last_abs = {em: dict(v) for em, v in users_abs.items()}

            self._tick_n += 1
            if self._tick_n % _SAVE_EVERY == 0:
                self._save_today_base()

    def get_today(self) -> Dict[str, dict]:
        today = date.today().isoformat()
        with _LOCK:
            rows = self._conn.execute(
                "SELECT email, up, dn FROM daily_traffic WHERE date=?", (today,)
            ).fetchall()
            return {r[0]: {"up": r[1], "dn": r[2]} for r in rows}

    def get_period(self, n_days: int) -> Dict[str, dict]:
        """Суммарный трафик за последние N дней."""
        cutoff = (date.today() - timedelta(days=n_days - 1)).isoformat()
        with _LOCK:
            rows = self._conn.execute(
                "SELECT email, SUM(up), SUM(dn) FROM daily_traffic"
                " WHERE date >= ? GROUP BY email",
                (cutoff,),
            ).fetchall()
            return {r[0]: {"up": r[1] or 0, "dn": r[2] or 0} for r in rows}

    def get_weekly(self) -> Dict[str, dict]:
        return self.get_period(7)

    def get_monthly(self) -> Dict[str, dict]:
        return self.get_period(30)

    def available_days(self) -> int:
        with _LOCK:
            row = self._conn.execute(
                "SELECT COUNT(DISTINCT date) FROM daily_traffic"
            ).fetchone()
            return row[0] if row else 0

    def get_period_hours(self, hours: int) -> Dict[str, dict]:
        """Суммарный трафик за последние N часов (только за сегодня)."""
        # SQLite не хранит часовую детализацию — возвращаем сегодняшние данные
        return self.get_today()

    # ── SNI Radar: IP-трафик ─────────────────────────────────

    def save_ip_bytes(self, ip_bytes: dict, email_for_ip: dict | None = None) -> None:
        """Сохраняет накопленные байты по IP.

        ip_bytes: {ip: [up, dn]}  — в памяти (накопленные с загрузки из БД).
        email_for_ip: {ip: email} — опционально, для связи IP с пользователем.
        """
        if not ip_bytes:
            return
        import time as _time
        now = int(_time.time())
        rows = [
            (ip, (email_for_ip or {}).get(ip, ""), int(vals[0]), int(vals[1]), now)
            for ip, vals in ip_bytes.items()
            if vals[0] > 0 or vals[1] > 0
        ]
        if not rows:
            return
        with _LOCK:
            with self._conn:
                self._conn.executemany(
                    "INSERT INTO ip_traffic(ip, email, up, dn, updated) VALUES(?,?,?,?,?)"
                    " ON CONFLICT(ip) DO UPDATE SET"
                    " email=excluded.email, up=excluded.up, dn=excluded.dn,"
                    " updated=excluded.updated",
                    rows,
                )

    def load_ip_bytes(self) -> dict:
        """Загружает [up, dn] по каждому IP из БД → {ip: [up, dn]}."""
        with _LOCK:
            rows = self._conn.execute(
                "SELECT ip, up, dn FROM ip_traffic"
            ).fetchall()
        return {r[0]: [r[1], r[2]] for r in rows}

    def save_ip_sni(self, sni_buf: dict) -> None:
        """Сохраняет новые SNI-хиты.

        sni_buf: {ip: {domain: (tag, count, last_ts)}}
        """
        if not sni_buf:
            return
        rows = []
        for ip, domains in sni_buf.items():
            for domain, (tag, count, last_ts) in domains.items():
                rows.append((ip, domain, tag, count, int(last_ts)))
        if not rows:
            return
        with _LOCK:
            with self._conn:
                self._conn.executemany(
                    "INSERT INTO ip_sni(ip, domain, tag, hits, last_seen) VALUES(?,?,?,?,?)"
                    " ON CONFLICT(ip, domain) DO UPDATE SET"
                    " tag=excluded.tag,"
                    " hits=hits+excluded.hits,"
                    " last_seen=MAX(last_seen, excluded.last_seen)",
                    rows,
                )
        # Ротация: удаляем старые записи (старше 30 дней)
        import time as _time
        cutoff = int(_time.time()) - 86400 * 30
        with _LOCK:
            with self._conn:
                self._conn.execute(
                    "DELETE FROM ip_sni WHERE last_seen < ?", (cutoff,)
                )

    def load_ip_sni(self) -> dict:
        """Загружает SNI-данные из БД → {ip: [(domain, tag, hits, last_seen), ...]}."""
        from collections import deque
        with _LOCK:
            rows = self._conn.execute(
                "SELECT ip, domain, tag, hits, last_seen FROM ip_sni"
                " ORDER BY last_seen DESC"
            ).fetchall()
        result: dict = {}
        for ip, domain, tag, hits, last_seen in rows:
            if ip not in result:
                result[ip] = deque(maxlen=50)
            result[ip].appendleft((domain, tag, hits, last_seen))
        return result

    def save_ip_connections(self, connections: dict) -> None:
        """Сохраняет временны́е метки подключений IP из лога.

        connections: {email: {ip: last_seen_ts}}
        Устанавливает first_seen только если ещё не задан (= 0).
        Обновляет last_active если новее.
        """
        import time as _t
        now = int(_t.time())
        rows = []
        for email, ips in connections.items():
            for ip, last_ts in ips.items():
                rows.append((ip, email, int(last_ts), int(last_ts), now))
        if not rows:
            return
        with _LOCK:
            with self._conn:
                self._conn.executemany(
                    "INSERT INTO ip_traffic(ip, email, first_seen, last_active, updated)"
                    " VALUES(?,?,?,?,?)"
                    " ON CONFLICT(ip) DO UPDATE SET"
                    "   email=excluded.email,"
                    "   first_seen=CASE WHEN first_seen=0 THEN excluded.first_seen"
                    "              ELSE first_seen END,"
                    "   last_active=MAX(last_active, excluded.last_active),"
                    "   updated=excluded.updated",
                    rows,
                )

    def query_all_ips(self) -> list:
        """Возвращает все IP-записи для таблицы IP-радара.

        Результат: [{ip, email, up, dn, first_seen, last_active}, ...]
        Сортировка: last_active DESC.
        """
        with _LOCK:
            rows = self._conn.execute(
                "SELECT ip, email, up, dn, first_seen, last_active"
                " FROM ip_traffic"
                " ORDER BY last_active DESC"
            ).fetchall()
        return [
            {
                "ip":          r[0],
                "email":       r[1],
                "up":          r[2],
                "dn":          r[3],
                "first_seen":  r[4],
                "last_active": r[5],
            }
            for r in rows
        ]

    def query_ip_sni(self, ip: str) -> list:
        """SNI-домены для одного IP из БД.

        Результат: [(domain, tag, hits, last_seen), ...] по убыванию hits.
        """
        with _LOCK:
            rows = self._conn.execute(
                "SELECT domain, tag, hits, last_seen FROM ip_sni"
                " WHERE ip=? ORDER BY hits DESC LIMIT 30",
                (ip,),
            ).fetchall()
        return list(rows)
