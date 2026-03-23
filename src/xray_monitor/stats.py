"""Xray stats collector, system stats, log tail."""

import os
import re
import time
import socket
import threading
from collections import deque, OrderedDict
from typing import Optional

try:
    import grpc
    _HAS_GRPC = True
except ImportError:
    grpc = None
    _HAS_GRPC = False

from .grpc_client import XrayGRPC
from .utils import HAS_PSUTIL

if HAS_PSUTIL:
    import psutil

_USER_HIST_MAX = 200  # max tracked users (LRU)
_TOP_BLOCKED_MAX = 500


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
    def __init__(self, server):
        self.server    = server
        self.channel   = None
        self.stub      = None
        self.connected = False
        self.error     = ""
        self._lock     = threading.Lock()
        self._prev: dict   = {}
        self._prev_t: float = 0
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

    def connect(self):
        if not _HAS_GRPC:
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
        cur = set(online_set)
        for u in cur - self._prev_online:
            self.conn_events.append(ConnEvent("connect", u))
        for u in self._prev_online - cur:
            self.conn_events.append(ConnEvent("disconnect", u))
        self._prev_online = cur

        # Prune _prev_ips for users no longer tracked
        stale = set(self._prev_ips.keys()) - set(user_ips.keys()) - cur
        for email in stale:
            del self._prev_ips[email]

        for email, ips in user_ips.items():
            cp = set(ips.keys()); pp = self._prev_ips.get(email, set())
            for ip in cp - pp:
                g = geo.fmt(ip) if geo else ""
                self.conn_events.append(ConnEvent("connect", email, ip, g))
            for ip in pp - cp:
                self.conn_events.append(ConnEvent("disconnect", email, ip))
            self._prev_ips[email] = cp

    def _update_user_hist(self, em, su, sd):
        """Update user history with LRU eviction."""
        if em in self.u_hist:
            self.u_hist.move_to_end(em)
        elif len(self.u_hist) >= _USER_HIST_MAX:
            self.u_hist.popitem(last=False)
        if em not in self.u_hist:
            self.u_hist[em] = UserHist()
        self.u_hist[em].add(su, sd)

    def _prune_speed(self, active_users: set):
        """Remove speed entries for users no longer active."""
        stale = set(self.u_speed.keys()) - active_users
        for em in stale:
            del self.u_speed[em]

    def reset(self):
        """Thread-safe reset of all counters."""
        with self._lock:
            if self.stub:
                self.stub.query_stats(pattern="", reset=True)
            self._prev.clear()
            self._prev_t = 0
            self.up_hist.clear()
            self.dn_hist.clear()
            self.u_speed.clear()
            self.peak_up = 0.0
            self.peak_dn = 0.0
            self.sess_up = 0.0
            self.sess_dn = 0.0

    def fetch(self, geo=None) -> dict:
        if not self.stub: self.connect()
        if not self.connected:
            return {"error": self.error or "Not connected"}
        R = {"time": time.time(), "inbounds": {}, "outbounds": {}, "users": {},
             "sys": {}, "online_users": [], "user_ips": {},
             "total_up": 0, "total_down": 0, "speed_up": 0.0, "speed_down": 0.0}
        try:
            with self._lock:
                cur: dict = {}
                for s in self.stub.query_stats():
                    n, val = s.get("name", ""), s.get("value", 0)
                    if not n: continue
                    cur[n] = val
                    parts = n.split(">>>")
                    if len(parts) == 4:
                        cat, tag, _, dir_ = parts
                        bk = R.get({"inbound": "inbounds", "outbound": "outbounds", "user": "users"}.get(cat))
                        if bk is not None:
                            if tag not in bk: bk[tag] = {"uplink": 0, "downlink": 0}
                            bk[tag][dir_] = val
                for ib in R["inbounds"].values():
                    R["total_up"]   += ib.get("uplink",   0)
                    R["total_down"] += ib.get("downlink",  0)
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
                    # Prune stale speed entries every 10 fetches
                    self._prune_speed(active_users)
                self.up_hist.append(R["speed_up"])
                self.dn_hist.append(R["speed_down"])
                self._prev = cur
                self._prev_t = now
            try: R["sys"] = self.stub.sys_stats()
            except Exception: pass
            try:
                R["online_users"] = self.stub.all_online_users()
                for em in R["users"]:
                    try:
                        ips = self.stub.online_ips(em)
                        if ips: R["user_ips"][em] = ips
                    except Exception: pass
                if geo: self._track(R["online_users"], R["user_ips"], geo)
            except Exception: pass
        except Exception as e:
            # Handle both grpc errors and general errors
            err_msg = str(e)
            if _HAS_GRPC and isinstance(e, grpc.RpcError):
                err_msg = f"gRPC: {e.code().name}"
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
            with open(self.path, "rb") as f:
                f.seek(0, 2); sz = f.tell()
                if sz < self._last_size:
                    self._last_pos = 0
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
            new_blocks = []
            for line in chunk.splitlines():
                ll = line.lower()
                if "-> block" not in ll and "->block" not in ll:
                    continue
                new_blocks.append(now)
                after_accepted = line
                acc_idx = ll.find(" accepted ")
                if acc_idx >= 0:
                    after_accepted = line[acc_idx + 10:]
                m = re.search(r"(?:tcp|udp):([^:,\s\[]+):(\d+)", after_accepted)
                if not m:
                    continue
                target = m.group(1).lower()
                port   = m.group(2)
                is_ip  = bool(re.match(r"^\d+\.\d+\.\d+\.\d+$", target))
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
                self._block_total   += len(new_blocks)
                self._block_session += len(new_blocks)
                self._block_window.extend(new_blocks)
        except Exception:
            pass

    def block_per_min(self) -> float:
        with self._lock:
            if not self._block_window: return 0.0
            now = time.time()
            cutoff = now - 300
            # Single pass count — no list copy
            count = sum(1 for t in self._block_window if t > cutoff)
            if count == 0: return 0.0
            # Approximate elapsed from oldest relevant entry
            oldest = next((t for t in self._block_window if t > cutoff), now)
            elapsed = now - oldest
            return count / max(elapsed / 60.0, 0.017)

    def top_blocked(self, n: int = 5) -> list:
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

            try:
                conns = psutil.net_connections(kind="tcp")
                d["tcp_est"]    = sum(1 for c in conns if c.status == "ESTABLISHED")
                d["tcp_listen"] = sum(1 for c in conns if c.status == "LISTEN")
            except (psutil.AccessDenied, OSError):
                d["tcp_est"] = 0
                d["tcp_listen"] = 0

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

            # Top processes by memory — single pass, collect top 12 only
            procs = []
            for proc in psutil.process_iter(["pid", "name", "cpu_percent", "memory_info"]):
                try:
                    mi = proc.info["memory_info"]
                    mem = mi.rss if mi else 0
                    cpu_p = proc.info["cpu_percent"] or 0.0
                    procs.append((proc.info["pid"], proc.info["name"] or "?", cpu_p, mem))
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            procs.sort(key=lambda x: x[3], reverse=True)
            d["top_procs"] = procs[:12]
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
