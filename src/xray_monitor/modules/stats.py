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

        user_ips  — IP от gRPC GetStatsOnlineIpList (только текущие активные)
        log_ips   — IP из access.log — используются ТОЛЬКО для обогащения событий IP,
                    НЕ записываются в _prev_ips (чтобы не помечать исторические IP онлайн)
        """
        cur = set(online_set)

        # Удаляем устаревших пользователей из _prev_ips
        stale = set(self._prev_ips.keys()) - set(user_ips.keys()) - cur
        for email in stale:
            self._prev_ips.pop(email, None)

        users_with_ip_events: set = set()
        for email, ips_dict in user_ips.items():
            ip_set = set(ips_dict.keys())
            pp = self._prev_ips.get(email, set())
            for ip in ip_set - pp:
                self.conn_events.append(ConnEvent("connect", email, ip))
                users_with_ip_events.add(email)
            for ip in pp - ip_set:
                self.conn_events.append(ConnEvent("disconnect", email, ip))
                users_with_ip_events.add(email)
            self._prev_ips[email] = ip_set

        # Для пользователей без gRPC IP — ищем последний IP из лога
        def _latest_log_ip(email: str) -> str:
            if not log_ips or email not in log_ips:
                return ""
            ips = log_ips[email]
            if not ips:
                return ""
            return max(ips, key=lambda ip: ips[ip])

        for u in cur - self._prev_online:
            if u not in users_with_ip_events:
                self.conn_events.append(ConnEvent("connect", u, _latest_log_ip(u)))
        for u in self._prev_online - cur:
            if u not in users_with_ip_events:
                self.conn_events.append(ConnEvent("disconnect", u, _latest_log_ip(u)))

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
                    if len(parts) == 4:
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
                    if self._fetch_n % _PRUNE_INTERVAL == 0:
                        self._prune_stale(active_users)

                self.up_hist.append(R["speed_up"])
                self.dn_hist.append(R["speed_down"])
                self._prev   = cur
                self._prev_t = now

            # Вне основного лока — независимые gRPC-вызовы
            try: R["sys"] = stub.sys_stats()
            except Exception: pass

            try:
                R["online_users"] = stub.all_online_users()
                for em in R["users"]:
                    try:
                        ips = stub.online_ips(em)
                        if ips: R["user_ips"][em] = ips
                    except Exception:
                        pass
                with self._lock:
                    self._track(R["online_users"], R["user_ips"],
                                log_ips=log_ips)
            except Exception:
                pass

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
