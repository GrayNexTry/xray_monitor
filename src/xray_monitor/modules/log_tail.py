"""Мониторинг лога доступа Xray и статистики блокировок."""

from __future__ import annotations

import os
import re
import time
import threading
from collections import deque, OrderedDict

_TOP_BLOCKED_MAX = 500
_RE_TRANSPORT    = re.compile(r"(?:tcp|udp):([^:,\s\[]+):(\d+)")
_RE_IPV4         = re.compile(r"^\d+\.\d+\.\d+\.\d+$")


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

    def read(self) -> list:
        try:
            if not os.path.exists(self.path): return []
            with open(self.path, "rb") as f:
                f.seek(0, 2); sz = f.tell(); f.seek(max(0, sz - 65536))
                return f.read().decode("utf-8", errors="replace").strip().split("\n")[-self.n:]
        except Exception:
            return []

    def update_block_stats(self) -> None:
        try:
            if not os.path.exists(self.path): return

            try:
                st = os.stat(self.path)
                current_inode = st.st_ino
                if self._last_inode and current_inode != self._last_inode:
                    self._last_pos = 0
                self._last_inode = current_inode
            except (AttributeError, OSError):
                pass

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

            while len(self._top_blocked) > _TOP_BLOCKED_MAX:
                self._top_blocked.popitem(last=False)

            with self._lock:
                self._block_total   += block_count
                self._block_session += block_count
                self._block_window.extend([now] * block_count)
        except Exception:
            pass

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
