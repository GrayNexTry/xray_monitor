"""Системная статистика (CPU, RAM, диск, сеть, процессы) через psutil."""

from __future__ import annotations

import os
import socket
import threading
import time
from typing import Any, Optional

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    psutil: Any = None  # type: ignore[assignment]
    HAS_PSUTIL = False


class SysStats:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._d:   dict  = {}
        self._net_prev: Any   = None
        self._net_t:    float = 0
        self._ping:     dict  = {}
        self._ping_t:   dict  = {}
        self._xray_pid:         Optional[int] = None
        self._xray_pid_check_t: float         = 0
        self._tcp_cache: tuple = (0, 0, 0.0)   # (est, listen, timestamp)

    def _find_xray_pid(self) -> Optional[int]:
        """Находит PID xray — кэш 30 секунд."""
        if not HAS_PSUTIL or psutil is None:
            return None
        now = time.time()
        if self._xray_pid and now - self._xray_pid_check_t < 30:
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

    def collect(self) -> dict:
        if not HAS_PSUTIL or psutil is None:
            return {}
        d: dict = {}
        try:
            d["cpu"]       = psutil.cpu_percent(interval=None)
            d["cpu_cores"] = psutil.cpu_percent(interval=None, percpu=True)
            try:
                d["load"] = os.getloadavg()  # type: ignore[attr-defined]
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

            net = psutil.net_io_counters()
            now = time.time()
            if self._net_prev and now - self._net_t > 0:
                dt = now - self._net_t
                d["rx_s"] = (net.bytes_recv - self._net_prev.bytes_recv) / dt
                d["tx_s"] = (net.bytes_sent - self._net_prev.bytes_sent) / dt
            d["rx_tot"] = net.bytes_recv
            d["tx_tot"] = net.bytes_sent
            self._net_prev = net
            self._net_t    = now

            # TCP-соединения — кэш 10 секунд (дорогой вызов)
            est, listen, tcp_ts = self._tcp_cache
            if now - tcp_ts > 10:
                try:
                    conns  = psutil.net_connections(kind="tcp")
                    est    = sum(1 for c in conns if c.status == "ESTABLISHED")
                    listen = sum(1 for c in conns if c.status == "LISTEN")
                    self._tcp_cache = (est, listen, now)
                except (psutil.AccessDenied, OSError):
                    est = listen = 0
            d["tcp_est"]    = est
            d["tcp_listen"] = listen
            d["procs"]      = len(psutil.pids())

            try:
                temps = psutil.sensors_temperatures()  # type: ignore[attr-defined]
                flat  = [e.current for vals in temps.values() for e in vals]
                if flat: d["temp"] = max(flat)
            except Exception:
                pass

            xray_pid = self._find_xray_pid()
            if xray_pid:
                try:
                    p = psutil.Process(xray_pid)
                    d["xray_pid"] = xray_pid
                    d["xray_cpu"] = p.cpu_percent(interval=None)
                    d["xray_mem"] = p.memory_info().rss
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    self._xray_pid = None

            import heapq
            top_n = 12
            procs: list = []
            for proc in psutil.process_iter(["pid", "name", "cpu_percent", "memory_info"]):
                try:
                    mi    = proc.info["memory_info"]
                    mem   = mi.rss if mi else 0
                    cpu_p = proc.info["cpu_percent"] or 0.0
                    entry = (mem, proc.info["pid"], proc.info["name"] or "?", cpu_p)
                    if len(procs) < top_n:
                        heapq.heappush(procs, entry)
                    elif mem > procs[0][0]:
                        heapq.heapreplace(procs, entry)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            d["top_procs"] = sorted(
                [(pid, name, cpu_p, mem) for mem, pid, name, cpu_p in procs],
                key=lambda x: x[3], reverse=True,
            )
        except Exception as e:
            d["error"] = str(e)

        with self._lock:
            self._d = d
        return d

    def get(self) -> dict:
        with self._lock:
            return dict(self._d)

    def ping(self, host: str, timeout: int = 2) -> Optional[float]:
        now = time.time()
        if host in self._ping_t and now - self._ping_t[host] < 15:
            return self._ping.get(host)
        self._ping_t[host] = now

        def _do() -> None:
            try:
                t0 = time.time()
                s  = socket.create_connection((host, 443), timeout=timeout)
                s.close()
                self._ping[host] = (time.time() - t0) * 1000
            except Exception:
                self._ping[host] = -1

        threading.Thread(target=_do, daemon=True).start()
        return self._ping.get(host)
