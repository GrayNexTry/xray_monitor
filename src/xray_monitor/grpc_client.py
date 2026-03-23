"""gRPC client for Xray Stats API."""

from .proto import iter_fields, encode_string, encode_bool


class XrayGRPC:
    def __init__(self, ch):
        B = "/xray.app.stats.command.StatsService/"
        mk = lambda m: ch.unary_unary(
            B + m, request_serializer=lambda x: x, response_deserializer=lambda x: x)
        self._q = mk("QueryStats")
        self._s = mk("GetSysStats")
        self._ou = mk("GetAllOnlineUsers")
        self._oi = mk("GetStatsOnlineIpList")

    def query_stats(self, pattern="", reset=False):
        p = b""
        if pattern: p += encode_string(1, pattern)
        if reset:   p += encode_bool(2, True)
        stats = []
        for fn, wt, v in iter_fields(self._q(p, timeout=5)):
            if fn == 1 and wt == 2:
                n = ""; val = 0
                for f2, w2, v2 in iter_fields(v):
                    if f2 == 1 and w2 == 2: n   = v2.decode("utf-8", errors="replace")
                    elif f2 == 2 and w2 == 0: val = v2
                stats.append({"name": n, "value": val})
        return stats

    def sys_stats(self):
        fm = {1: "goroutines", 2: "gc_runs", 3: "alloc", 4: "total_alloc",
              5: "sys", 6: "mallocs", 7: "frees", 8: "live_objects", 9: "pause_ns", 10: "uptime"}
        r = {}
        for fn, wt, v in iter_fields(self._s(b"", timeout=5)):
            if fn in fm: r[fm[fn]] = v
        return r

    def all_online_users(self):
        u = []
        for fn, wt, v in iter_fields(self._ou(b"", timeout=5)):
            if fn == 1 and wt == 2:
                u.append(v.decode("utf-8", errors="replace"))
        return u

    def online_ips(self, email):
        ips = {}
        try:
            for fn, wt, v in iter_fields(
                    self._oi(encode_string(1, f"user>>>{email}>>>online"), timeout=5)):
                if fn == 2 and wt == 2:
                    k = ""; val = 0
                    for f2, w2, v2 in iter_fields(v):
                        if f2 == 1 and w2 == 2: k   = v2.decode("utf-8", errors="replace")
                        elif f2 == 2 and w2 == 0: val = v2
                    if k: ips[k] = val
        except Exception:
            pass
        return ips
