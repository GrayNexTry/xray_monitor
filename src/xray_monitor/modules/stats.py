"""Сборщик статистики Xray через gRPC."""

from __future__ import annotations

import time
import threading
from collections import deque, OrderedDict
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    import grpc as _grpc_type

try:
    import grpc  # type: ignore[import-untyped]
    _HAS_GRPC = True
except ImportError:
    grpc: Any = None
    _HAS_GRPC = False

from .grpc_client import XrayGRPC
from ..DataTypes import UserHist, ConnEvent

_USER_HIST_MAX  = 200   # макс. отслеживаемых пользователей (LRU)
_PRUNE_INTERVAL = 10    # очистка устаревших записей каждые N выборок


class XrayStats:
    def __init__(self, server: str) -> None:
        self.server    = server
        self.channel:  Any             = None
        self.stub:     Optional[XrayGRPC] = None
        self.connected = False
        self.error     = ""
        self._lock     = threading.Lock()
        self._prev:    dict  = {}
        self._prev_t:  float = 0
        self._fetch_n: int   = 0

        self.up_hist: deque = deque(maxlen=90)
        self.dn_hist: deque = deque(maxlen=90)
        self.u_speed: dict  = {}                # email -> {su, sd}
        self.u_hist:  OrderedDict = OrderedDict()  # LRU-ограниченный
        self.peak_up  = 0.0
        self.peak_dn  = 0.0
        self.sess_up  = 0.0
        self.sess_dn  = 0.0

        self._prev_online: set  = set()
        self.conn_events:  deque = deque(maxlen=200)
        self._prev_ips:    dict  = {}
        self._prev_log_ips: dict = {}   # email -> {ip: ts} — предыдущий снимок лога
        self._log_initialized: bool = False  # первый снимок не генерирует события

        # SNI Radar: накопленные байты по IP (пропорциональная оценка)
        # {ip: [up, dn]}  — начальное значение загружается из БД при старте
        self.ip_bytes: dict = {}
        # Маппинг ip -> email (для БД)
        self.ip_email: dict = {}

    # ── Подключение ──────────────────────────────────────────

    def connect(self) -> None:
        if not _HAS_GRPC or grpc is None:
            self.connected = False
            self.error = "grpc не установлен"
            return
        try:
            if self.channel:
                try: self.channel.close()
                except Exception: pass
            opts = [
                ('grpc.keepalive_time_ms',             10000),
                ('grpc.keepalive_timeout_ms',           5000),
                ('grpc.keepalive_permit_without_calls', True),
                ('grpc.connect_timeout_ms',             3000),
                ('grpc.max_receive_message_length',     4 * 1024 * 1024),
            ]
            self.channel   = grpc.insecure_channel(self.server, options=opts)
            self.stub      = XrayGRPC(self.channel)
            self.connected = True
            self.error     = ""
        except Exception as e:
            self.connected = False
            self.error     = str(e)

    def disconnect(self) -> None:
        if self.channel:
            try: self.channel.close()
            except Exception: pass
        self.connected = False

    # ── Внутренние вспомогательные методы ────────────────────

    def _track(self, online_set: list, user_ips: dict,
               log_ips: dict | None = None) -> None:
        """Отслеживает события подключения/отключения. Вызывается под self._lock.

        Источники (от надёжного к менее надёжному):
          1. log_ips (access.log) — connect-события по новым IP
          2. user_ips (gRPC GetStatsOnlineIpList) — disconnect-события по исчезнувшим IP
          3. online_set (gRPC GetAllOnlineUsers / QueryStats) — user-level события без IP
        """
        cur = set(online_set)

        # ── 1. Log-based IP connect detection ────────────────
        # Новый IP в логе = новое подключение клиента
        already_from_log: set = set()   # (email, ip) уже обработанные через лог
        if log_ips is not None:
            if not self._log_initialized:
                # Первый снимок: просто запоминаем, события не создаём
                self._prev_log_ips = {
                    em: dict(ips) for em, ips in log_ips.items()
                }
                self._log_initialized = True
            else:
                for email, ip_ts in log_ips.items():
                    prev = self._prev_log_ips.get(email, {})
                    for ip, ts in ip_ts.items():
                        if ip not in prev:
                            # Новый IP — connect
                            self.conn_events.append(ConnEvent("connect", email, ip))
                            already_from_log.add((email, ip))
                        elif ts > prev[ip] + 30:
                            # Тот же IP, но существенно более новый timestamp —
                            # переподключение: сначала disconnect, потом connect
                            self.conn_events.append(ConnEvent("disconnect", email, ip))
                            self.conn_events.append(ConnEvent("connect", email, ip))
                            already_from_log.add((email, ip))
                self._prev_log_ips = {
                    em: dict(ips) for em, ips in log_ips.items()
                }

        # ── 2. gRPC IP-level tracking (для disconnect и online-статуса) ──
        def _latest_log_ip(email: str) -> str:
            if not log_ips or email not in log_ips:
                return ""
            ips = log_ips[email]
            return max(ips, key=lambda ip: ips[ip]) if ips else ""

        stale = set(self._prev_ips.keys()) - set(user_ips.keys()) - cur
        for email in stale:
            self._prev_ips.pop(email, None)

        users_with_grpc_events: set = set()
        for email, ips_dict in user_ips.items():
            ip_set = set(ips_dict.keys())
            pp = self._prev_ips.get(email, set())
            for ip in ip_set - pp:
                if (email, ip) not in already_from_log:
                    self.conn_events.append(ConnEvent("connect", email, ip))
                users_with_grpc_events.add(email)
            for ip in pp - ip_set:
                self.conn_events.append(ConnEvent("disconnect", email, ip))
                users_with_grpc_events.add(email)
            self._prev_ips[email] = ip_set

        # ── 3. User-level connect/disconnect ──────────────────
        log_users = set(log_ips.keys()) if log_ips else set()
        for u in cur - self._prev_online:
            # connect пропускаем если лог уже сгенерил событие по IP
            if u not in users_with_grpc_events and u not in log_users:
                self.conn_events.append(ConnEvent("connect", u, ""))
        for u in self._prev_online - cur:
            # disconnect всегда — лог не умеет их детектировать
            if u not in users_with_grpc_events:
                self.conn_events.append(ConnEvent("disconnect", u,
                                                  _latest_log_ip(u)))

        self._prev_online = cur

    def _update_user_hist(self, em: str, su: float, sd: float) -> None:
        """Обновляет историю пользователя с LRU-вытеснением. Под self._lock."""
        if em in self.u_hist:
            self.u_hist.move_to_end(em)
        elif len(self.u_hist) >= _USER_HIST_MAX:
            self.u_hist.popitem(last=False)
        if em not in self.u_hist:
            self.u_hist[em] = UserHist()
        self.u_hist[em].add(su, sd)

    def _prune_stale(self, active_users: set) -> None:
        """Удаляет записи скоростей/истории для неактивных пользователей. Под self._lock."""
        for em in set(self.u_speed.keys()) - active_users:
            self.u_speed.pop(em, None)
        stale_hist = set(self.u_hist.keys()) - active_users
        if len(stale_hist) > _USER_HIST_MAX // 2:
            for em in list(stale_hist)[:len(stale_hist) - _USER_HIST_MAX // 4]:
                self.u_hist.pop(em, None)

    # ── Публичный API ─────────────────────────────────────────

    def reset(self) -> None:
        """Потокобезопасный сброс всех счётчиков."""
        with self._lock:
            if self.stub:
                try: self.stub.query_stats(pattern="", reset=True)
                except Exception: pass
            self._prev.clear()
            self._prev_t  = 0
            self._fetch_n = 0
            self.up_hist.clear()
            self.dn_hist.clear()
            self.u_speed.clear()
            self.u_hist.clear()
            self.peak_up  = 0.0
            self.peak_dn  = 0.0
            self.sess_up  = 0.0
            self.sess_dn  = 0.0

    def fetch(self, log_ips: dict | None = None) -> dict:
        if not self.stub:
            self.connect()
        stub = self.stub
        if not self.connected or stub is None:
            return {"error": self.error or "Нет подключения"}

        R: dict = {
            "time": time.time(), "inbounds": {}, "outbounds": {}, "users": {},
            "sys": {}, "online_users": [], "user_ips": {},
            "total_up": 0, "total_down": 0, "speed_up": 0.0, "speed_down": 0.0,
        }
        _qs_online: list = []
        try:
            with self._lock:
                self._fetch_n += 1
                cur: dict = {}
                _cat_map = {"inbound": "inbounds", "outbound": "outbounds", "user": "users"}
                for s in stub.query_stats():
                    n, val = s.get("name", ""), s.get("value", 0)
                    if not n: continue
                    cur[n] = val
                    parts = n.split(">>>")
                    # user>>>email>>>online  (3 части, val=1 если онлайн)
                    if (len(parts) == 3 and parts[0] == "user"
                            and parts[2] == "online" and val):
                        _qs_online.append(parts[1])
                    elif len(parts) == 4:
                        cat, tag, _, dir_ = parts
                        bucket_key = _cat_map.get(cat)
                        if bucket_key:
                            bk = R[bucket_key]
                            if tag not in bk: bk[tag] = {"uplink": 0, "downlink": 0}
                            bk[tag][dir_] = val

                for ib in R["inbounds"].values():
                    R["total_up"]   += ib.get("uplink",   0)
                    R["total_down"] += ib.get("downlink", 0)

                now = time.time()
                dt  = now - self._prev_t if self._prev_t > 0 else 0
                if dt > 0:
                    up_p = sum(v for k, v in self._prev.items()
                               if ">>>traffic>>>uplink" in k and k.startswith("inbound>>>"))
                    dn_p = sum(v for k, v in self._prev.items()
                               if ">>>traffic>>>downlink" in k and k.startswith("inbound>>>"))
                    R["speed_up"]   = max(0, (R["total_up"]   - up_p) / dt)
                    R["speed_down"] = max(0, (R["total_down"] - dn_p) / dt)
                    self.peak_up    = max(self.peak_up, R["speed_up"])
                    self.peak_dn    = max(self.peak_dn, R["speed_down"])
                    self.sess_up   += R["speed_up"]   * dt
                    self.sess_dn   += R["speed_down"] * dt
                    active_users    = set(R["users"].keys())
                    for em, ud in R["users"].items():
                        pu = self._prev.get(f"user>>>{em}>>>traffic>>>uplink",   0)
                        pd = self._prev.get(f"user>>>{em}>>>traffic>>>downlink", 0)
                        su = max(0, (ud["uplink"]   - pu) / dt)
                        sd = max(0, (ud["downlink"] - pd) / dt)
                        self.u_speed[em] = {"su": su, "sd": sd}
                        self._update_user_hist(em, su, sd)

                        # ── Per-IP byte accumulation ──────────────
                        # Распределяем трафик ТОЛЬКО на активные IP:
                        # 1) IP подтверждённые gRPC (self._prev_ips) → самые надёжные
                        # 2) Иначе — единственный самый свежий IP из лога
                        # Это предотвращает размазывание трафика по всем IP за 24 ч.
                        delta_up = max(0, ud["uplink"]   - pu)
                        delta_dn = max(0, ud["downlink"] - pd)
                        if (delta_up > 0 or delta_dn > 0) and log_ips:
                            log_em = log_ips.get(em) or {}
                            grpc_online_ips = self._prev_ips.get(em, set())
                            if grpc_online_ips:
                                # Пересечение с логом; если пусто — берём gRPC IPs как есть
                                active_ips = [ip for ip in grpc_online_ips if ip in log_em]
                                if not active_ips:
                                    active_ips = list(grpc_online_ips)
                            elif log_em:
                                # Только самый свежий IP из лога
                                active_ips = [max(log_em, key=log_em.get)]  # type: ignore[arg-type]
                            else:
                                active_ips = []
                            if active_ips:
                                per = len(active_ips)
                                for ip in active_ips:
                                    self.ip_email[ip] = em
                                    entry = self.ip_bytes.get(ip)
                                    if entry is None:
                                        self.ip_bytes[ip] = [
                                            delta_up / per,
                                            delta_dn / per,
                                        ]
                                    else:
                                        entry[0] += delta_up / per
                                        entry[1] += delta_dn / per

                    if self._fetch_n % _PRUNE_INTERVAL == 0:
                        self._prune_stale(active_users)

                self.up_hist.append(R["speed_up"])
                self.dn_hist.append(R["speed_down"])
                self._prev   = cur
                self._prev_t = now

            # Вне основного лока — независимые gRPC-вызовы
            try: R["sys"] = stub.sys_stats()
            except Exception: pass

            # Дополняем онлайн-список из GetAllOnlineUsers (если поддерживается)
            try:
                grpc_online = stub.all_online_users()
                # Объединяем: QueryStats + GetAllOnlineUsers (без дублей)
                merged = list(dict.fromkeys(_qs_online + grpc_online))
                R["online_users"] = merged if merged else _qs_online
            except Exception:
                R["online_users"] = _qs_online

            try:
                for em in R["users"]:
                    try:
                        ips = stub.online_ips(em)
                        if ips: R["user_ips"][em] = ips
                    except Exception:
                        pass
            except Exception:
                pass

            # _track вызывается всегда — QueryStats всегда доступен
            with self._lock:
                self._track(R["online_users"], R["user_ips"],
                            log_ips=log_ips)

        except Exception as e:
            err_msg = str(e)
            if _HAS_GRPC and grpc is not None:
                try:
                    if isinstance(e, grpc.RpcError):
                        rpc_error: _grpc_type.RpcError = e  # type: ignore[assignment]
                        err_msg = f"gRPC: {rpc_error.code().name}"
                except Exception:
                    pass
            self.connected = False
            self.error     = err_msg
            R["error"]     = self.error
        return R
