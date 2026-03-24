"""Общие типы данных приложения."""

from __future__ import annotations

import time
from collections import deque


class UserHist:
    """История скоростей пользователя (LRU)."""
    __slots__ = ('up', 'dn', 'p_up', 'p_dn', 'n')

    def __init__(self, maxlen: int = 45) -> None:
        self.up   = deque(maxlen=maxlen)
        self.dn   = deque(maxlen=maxlen)
        self.p_up = 0.0
        self.p_dn = 0.0
        self.n    = 0

    def add(self, su: float, sd: float) -> None:
        self.up.append(su)
        self.dn.append(sd)
        self.p_up = max(self.p_up, su)
        self.p_dn = max(self.p_dn, sd)
        self.n += 1


class ConnEvent:
    """Событие подключения / отключения пользователя."""
    __slots__ = ('kind', 'email', 'ip', 'geo', 'ts')

    def __init__(self, kind: str, email: str, ip: str = "", geo: str = "") -> None:
        self.kind  = kind
        self.email = email
        self.ip    = ip
        self.geo   = geo
        self.ts    = time.time()
