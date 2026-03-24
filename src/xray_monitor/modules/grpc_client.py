"""gRPC-клиент для Xray Stats API."""

from __future__ import annotations

from typing import Any

from .proto import iter_fields, encode_string, encode_bool


class XrayGRPC:
    def __init__(self, channel: Any) -> None:
        self._ch = channel

    def _call(self, method: str, body: bytes) -> bytes:
        stub = self._ch.unary_unary(
            f"/xray.app.stats.command.StatsService/{method}",
            request_serializer=lambda x: x,
            response_deserializer=lambda x: x,
        )
        return stub(body, timeout=5)

    def query_stats(self, pattern: str = "", reset: bool = False) -> list:
        body = encode_string(1, pattern) + encode_bool(2, reset)
        raw  = self._call("QueryStats", body)
        results = []
        for fn, wt, val in iter_fields(raw):
            if fn == 1 and wt == 2:
                item: dict = {}
                for f2, w2, v2 in iter_fields(val):
                    if f2 == 1 and w2 == 2:
                        item["name"] = v2.decode("utf-8", errors="replace")
                    elif f2 == 2 and w2 == 0:
                        item["value"] = v2
                if item:
                    results.append(item)
        return results

    def sys_stats(self) -> dict:
        raw = self._call("GetSysStats", b"")
        d: dict = {}
        field_map = {
            1: "goroutines", 2: "gc_runs", 3: "alloc", 4: "total_alloc",
            5: "sys",        6: "mallocs", 7: "frees", 8: "live_objects",
            9: "pause_ns",  10: "uptime",
        }
        for fn, _, val in iter_fields(raw):
            if fn in field_map:
                d[field_map[fn]] = val
        return d

    def all_online_users(self) -> list:
        raw = self._call("GetAllOnlineUsers", b"")
        users: list = []
        seen: set   = set()
        for fn, wt, val in iter_fields(raw):
            if fn == 1 and wt == 2:
                name = val.decode("utf-8", errors="replace")
                # Xray возвращает полный ключ статистики: "user>>>email@tag>>>online"
                # Нам нужна только средняя часть — email@tag
                parts = name.split(">>>")
                email = parts[1] if len(parts) >= 3 else name
                if email and email not in seen:
                    seen.add(email)
                    users.append(email)
        return users

    def online_ips(self, email: str) -> dict:
        body = encode_string(1, email)
        raw  = self._call("GetStatsOnlineIpList", body)
        ips: dict = {}
        for fn, wt, val in iter_fields(raw):
            if fn == 1 and wt == 2:
                for f2, w2, v2 in iter_fields(val):
                    if f2 == 1 and w2 == 2:
                        ip = v2.decode("utf-8", errors="replace")
                        ips[ip] = ips.get(ip, 0) + 1
        return ips
