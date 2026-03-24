"""Xray stats collector, system stats, log tail."""
from __future__ import annotations

import os
import re
import time
import socket
import threading
from collections import deque, OrderedDict
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    import grpc as _grpc_type
    import psutil as _psutil_type

try:
    import grpc  # type: ignore[import-untyped]
    _HAS_GRPC = True
except ImportError:
    grpc: Any = None
    _HAS_GRPC = False

from .grpc_client import XrayGRPC
from .utils import HAS_PSUTIL

try:
    import psutil  # type: ignore[import-untyped]
except ImportError:
    psutil: Any = None

_USER_HIST_MAX = 200   # max tracked users (LRU)
_TOP_BLOCKED_MAX = 500
_PRUNE_INTERVAL = 10   # prune stale entries every N fetches

# Pre-compiled regex for block stats parsing
_RE_TRANSPORT = re.compile(r"(?:tcp|udp):([^:,\s\[]+):(\d+)")
_RE_IPV4 = re.compile(r"^\d+\.\d+\.\d+\.\d+$")


class UserHist:
    __slots__ = ('up', 'dn', 'p_up', 'p_dn', 'n')

    def __init__(self, maxlen=45):
        self.up   = deque(maxlen=maxlen)
        self.dn   = deque(maxlen=maxlen)
        self.p_up = 0.0
        self.p_dn = 0.0
        self.n    = 0

    def add(self, su, sd):
        self.up.append(su); self.dn.append(sd)
        self.p_up = max(self.p_up, su)
        self.p_dn = max(self.p_dn, sd)
        self.n += 1


class ConnEvent:
    __slots__ = ('kind', 'email', 'ip', 'geo', 'ts')

    def __init__(self, kind, email, ip="", geo=""):
        self.kind  = kind
        self.email = email
        self.ip    = ip
        self.geo   = geo
        self.ts    = time.time()


class XrayStats:
    def __init__(self, server: str):
        self.server    = server
        self.channel: Any = None
        self.stub: Optional[XrayGRPC] = None
        self.connected = False
        self.error     = ""
        self._lock     = threading.Lock()
        self._prev: dict   = {}
        self._prev_t: float = 0
        self._fetch_n: int = 0
        self.up_hist = deque(maxlen=90)
        self.dn_hist = deque(maxlen=90)
        self.u_speed: dict = {}        # email -> {su, sd}
        self.u_hist: OrderedDict = OrderedDict()  # LRU bounded
        self.peak_up = 0.0
        self.peak_dn = 0.0
        self.sess_up = 0.0
        self.sess_dn = 0.0
        self._prev_online: set = set()
        self.conn_events: deque = deque(maxlen=200)
        self._prev_ips: dict = {}

    def connect(self) -> None:
        if not _HAS_GRPC or grpc is None:
            self.connected = False
            self.error = "grpc not installed"
            return
        try:
            if self.channel:
                try: self.channel.close()
                except Exception: pass
            _grpc_opts = [
                ('grpc.keepalive_time_ms',             10000),
                ('grpc.keepalive_timeout_ms',           5000),
                ('grpc.keepalive_permit_without_calls', True),
                ('grpc.connect_timeout_ms',             3000),
                ('grpc.max_receive_message_length',     4 * 1024 * 1024),
            ]
            self.channel = grpc.insecure_channel(self.server, options=_grpc_opts)
            self.stub      = XrayGRPC(self.channel)
            self.connected = True
            self.error     = ""
        except Exception as e:
            self.connected = False
            self.error = str(e)

    def disconnect(self):
        if self.channel:
            try: self.channel.close()
            except Exception: pass
        self.connected = False

    def _track(self, online_set, user_ips, geo):
        """Track connect/disconnect events. Must be called under self._lock."""
        cur = set(online_set)
        for u in cur - self._prev_online:
            self.conn_events.append(ConnEvent("connect", u))
        for u in self._prev_online - cur:
            self.conn_events.append(ConnEvent("disconnect", u))
        self._prev_online = cur

        # Prune _prev_ips for users no longer tracked
        stale = set(self._prev_ips.keys()) - set(user_ips.keys()) - cur
        for email in stale:
            self._prev_ips.pop(email, None)

        for email, ips in user_ips.items():
            cp = set(ips.keys()); pp = self._prev_ips.get(email, set())
            for ip in cp - pp:
                g = geo.fmt(ip) if geo else ""
                self.conn_events.append(ConnEvent("connect", email, ip, g))
            for ip in pp - cp:
                self.conn_events.append(ConnEvent("disconnect", email, ip))
            self._prev_ips[email] = cp

    def _update_user_hist(self, em, su, sd):
        """Update user history with LRU eviction. Must be called under self._lock."""
        if em in self.u_hist:
            self.u_hist.move_to_end(em)
        elif len(self.u_hist) >= _USER_HIST_MAX:
            self.u_hist.popitem(last=False)
        if em not in self.u_hist:
            self.u_hist[em] = UserHist()
        self.u_hist[em].add(su, sd)

    def _prune_stale(self, active_users: set):
        """Remove speed/hist entries for users no longer active. Under self._lock."""
        # Prune speed
        stale_speed = set(self.u_speed.keys()) - active_users
        for em in stale_speed:
            self.u_speed.pop(em, None)
        # Prune hist for long-gone users (keep last _USER_HIST_MAX)
        stale_hist = set(self.u_hist.keys()) - active_users
        if len(stale_hist) > _USER_HIST_MAX // 2:
            for em in list(stale_hist)[:len(stale_hist) - _USER_HIST_MAX // 4]:
                self.u_hist.pop(em, None)

    def reset(self):
        """Thread-safe reset of all counters."""
        with self._lock:
            if self.stub:
                try:
                    self.stub.query_stats(pattern="", reset=True)
                except Exception:
                    pass
            self._prev.clear()
            self._prev_t = 0
            self._fetch_n = 0
            self.up_hist.clear()
            self.dn_hist.clear()
            self.u_speed.clear()
            self.u_hist.clear()
            self.peak_up = 0.0
            self.peak_dn = 0.0
            self.sess_up = 0.0
            self.sess_dn = 0.0

    def fetch(self, geo: Any = None) -> dict:
        if not self.stub: self.connect()
        stub = self.stub
        if not self.connected or stub is None:
            return {"error": self.error or "Not connected"}
        R: dict = {"time": time.time(), "inbounds": {}, "outbounds": {}, "users": {},
             "sys": {}, "online_users": [], "user_ips": {},
             "total_up": 0, "total_down": 0, "speed_up": 0.0, "speed_down": 0.0}
        try:
            with self._lock:
                self._fetch_n += 1
                cur: dict = {}
                # Category lookup — avoid creating dict each iteration
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
                dt = now - self._prev_t if self._prev_t > 0 else 0
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
                    active_users = set(R["users"].keys())
                    for em, ud in R["users"].items():
                        pu = self._prev.get(f"user>>>{em}>>>traffic>>>uplink",   0)
                        pd = self._prev.get(f"user>>>{em}>>>traffic>>>downlink", 0)
                        su = max(0, (ud["uplink"]   - pu) / dt)
                        sd = max(0, (ud["downlink"] - pd) / dt)
                        self.u_speed[em] = {"su": su, "sd": sd}
                        self._update_user_hist(em, su, sd)
                    # Prune stale entries periodically
                    if self._fetch_n % _PRUNE_INTERVAL == 0:
                        self._prune_stale(active_users)

                self.up_hist.append(R["speed_up"])
                self.dn_hist.append(R["speed_down"])
                self._prev = cur
                self._prev_t = now

            # Outside main lock — these are independent gRPC calls
            try: R["sys"] = stub.sys_stats()
            except Exception: pass

            try:
                R["online_users"] = stub.all_online_users()
                for em in R["users"]:
                    try:
                        ips = stub.online_ips(em)
                        if ips: R["user_ips"][em] = ips
                    except Exception: pass
                # Track under lock since _track modifies shared state
                if geo:
                    with self._lock:
                        self._track(R["online_users"], R["user_ips"], geo)
            except Exception: pass

        except Exception as e:
            err_msg = str(e)
            # Safe grpc error check — only access grpc.RpcError if grpc is available
            if _HAS_GRPC and grpc is not None:
                try:
                    if isinstance(e, grpc.RpcError):
                        err_msg = f"gRPC: {e.code().name}"
                except Exception:
                    pass
            self.connected = False
            self.error = err_msg
            R["error"] = self.error
        return R


class LogTail:
    def __init__(self, path="/var/log/xray/access.log", n=80):
        self.path   = path
        self.n      = n
        self._lock  = threading.Lock()
        self._block_total   = 0
        self._block_session = 0
        self._block_window  = deque(maxlen=600)
        self._last_pos      = 0
        self._last_size     = 0
        self._last_inode    = 0  # Track inode for log rotation detection
        self._top_blocked: OrderedDict = OrderedDict()

    def read(self) -> list:
        try:
            if not os.path.exists(self.path): return []
            with open(self.path, "rb") as f:
                f.seek(0, 2); sz = f.tell(); f.seek(max(0, sz - 65536))
                return f.read().decode("utf-8", errors="replace").strip().split("\n")[-self.n:]
        except Exception:
            return []

    def update_block_stats(self):
        try:
            if not os.path.exists(self.path): return

            # Detect log rotation via inode change
            try:
                st = os.stat(self.path)
                current_inode = st.st_ino
                if self._last_inode and current_inode != self._last_inode:
                    # Log was rotated — reset position
                    self._last_pos = 0
                self._last_inode = current_inode
            except (AttributeError, OSError):
                pass  # st_ino not available on all platforms

            with open(self.path, "rb") as f:
                f.seek(0, 2); sz = f.tell()
                if sz < self._last_size:
                    self._last_pos = 0  # File truncated
                self._last_size = sz
                is_first_scan = (self._last_pos == 0)
                if is_first_scan:
                    f.seek(max(0, sz - 10 * 1024 * 1024))
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
            for line in chunk.splitlines():
                ll = line.lower()
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

            # Evict LRU entries if too many targets tracked
            while len(self._top_blocked) > _TOP_BLOCKED_MAX:
                self._top_blocked.popitem(last=False)

            with self._lock:
                self._block_total   += block_count
                self._block_session += block_count
                # Extend window with timestamps for rate calc
                self._block_window.extend([now] * block_count)
        except Exception:
            pass

    def block_per_min(self) -> float:
        with self._lock:
            if not self._block_window: return 0.0
            now = time.time()
            cutoff = now - 300
            # Single pass — count and find oldest in one go
            count = 0
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
            return sorted(self._top_blocked.items(), key=lambda x: x[1], reverse=True)[:n]


class SysStats:
    def __init__(self):
        self._lock = threading.Lock()
        self._d: dict = {}
        self._net_prev = None
        self._net_t: float = 0
        self._ping: dict = {}
        self._ping_t: dict = {}
        self._xray_pid: Optional[int] = None
        self._xray_pid_check_t: float = 0
        self._tcp_cache: tuple = (0, 0, 0.0)  # (est, listen, timestamp)

    def _find_xray_pid(self) -> Optional[int]:
        """Find xray PID — cached for 30 seconds."""
        now = time.time()
        if self._xray_pid and now - self._xray_pid_check_t < 30:
            # Verify it still exists
            try:
                p = psutil.Process(self._xray_pid)
                if p.is_running() and "xray" in (p.name() or "").lower():
                    return self._xray_pid
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
            self._xray_pid = None

        self._xray_pid_check_t = now
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                pname = (proc.info["name"] or "").lower()
                if "xray" in pname or "xray" in " ".join(proc.info["cmdline"] or []).lower():
                    self._xray_pid = proc.info["pid"]
                    return self._xray_pid
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return None

    def collect(self):
        if not HAS_PSUTIL: return {}
        d: dict = {}
        try:
            d["cpu"]       = psutil.cpu_percent(interval=None)
            d["cpu_cores"] = psutil.cpu_percent(interval=None, percpu=True)
            try:
                d["load"] = os.getloadavg()
            except (AttributeError, OSError):
                d["load"] = (0, 0, 0)
            vm = psutil.virtual_memory()
            d["ram_pct"]   = vm.percent
            d["ram_used"]  = vm.used
            d["ram_total"] = vm.total
            d["ram_free"]  = vm.available
            disk = psutil.disk_usage("/")
            d["disk_pct"]  = disk.percent
            d["disk_used"] = disk.used
            d["disk_tot"]  = disk.total

            net = psutil.net_io_counters(); now = time.time()
            if self._net_prev and now - self._net_t > 0:
                dt = now - self._net_t
                d["rx_s"] = (net.bytes_recv - self._net_prev.bytes_recv) / dt
                d["tx_s"] = (net.bytes_sent - self._net_prev.bytes_sent) / dt
            d["rx_tot"] = net.bytes_recv
            d["tx_tot"] = net.bytes_sent
            self._net_prev = net
            self._net_t = now

            # TCP connections — cache for 10 seconds (expensive call)
            est, listen, tcp_ts = self._tcp_cache
            if now - tcp_ts > 10:
                try:
                    conns = psutil.net_connections(kind="tcp")
                    est    = sum(1 for c in conns if c.status == "ESTABLISHED")
                    listen = sum(1 for c in conns if c.status == "LISTEN")
                    self._tcp_cache = (est, listen, now)
                except (psutil.AccessDenied, OSError):
                    est, listen = 0, 0
            d["tcp_est"]    = est
            d["tcp_listen"] = listen

            d["procs"] = len(psutil.pids())

            try:
                temps = psutil.sensors_temperatures()
                flat = [e.current for vals in temps.values() for e in vals]
                if flat: d["temp"] = max(flat)
            except Exception: pass

            # Xray process — use cached PID
            xray_pid = self._find_xray_pid()
            if xray_pid:
                try:
                    p = psutil.Process(xray_pid)
                    d["xray_pid"] = xray_pid
                    d["xray_cpu"] = p.cpu_percent(interval=None)
                    d["xray_mem"] = p.memory_info().rss
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    self._xray_pid = None

            # Top processes by memory — use heap for efficiency with large proc lists
            import heapq
            top_n = 12
            procs = []
            for proc in psutil.process_iter(["pid", "name", "cpu_percent", "memory_info"]):
                try:
                    mi = proc.info["memory_info"]
                    mem = mi.rss if mi else 0
                    cpu_p = proc.info["cpu_percent"] or 0.0
                    entry = (mem, proc.info["pid"], proc.info["name"] or "?", cpu_p)
                    if len(procs) < top_n:
                        heapq.heappush(procs, entry)
                    elif mem > procs[0][0]:
                        heapq.heapreplace(procs, entry)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            # Convert to expected format: (pid, name, cpu, mem)
            d["top_procs"] = sorted(
                [(pid, name, cpu_p, mem) for mem, pid, name, cpu_p in procs],
                key=lambda x: x[3], reverse=True
            )
        except Exception as e:
            d["error"] = str(e)
        with self._lock:
            self._d = d
        return d

    def get(self) -> dict:
        with self._lock: return dict(self._d)

    def ping(self, host: str, timeout: int = 2) -> Optional[float]:
        now = time.time()
        if host in self._ping_t and now - self._ping_t[host] < 15:
            return self._ping.get(host)
        self._ping_t[host] = now

        def _do():
            try:
                t0 = time.time()
                s  = socket.create_connection((host, 443), timeout=timeout)
                s.close()
                self._ping[host] = (time.time() - t0) * 1000
            except Exception:
                self._ping[host] = -1
        threading.Thread(target=_do, daemon=True).start()
        return self._ping.get(host)
