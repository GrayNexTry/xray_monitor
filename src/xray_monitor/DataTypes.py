"""Общие типы данных приложения."""

from __future__ import annotations

import time
from collections import deque
from typing import Literal

# Размер кольцевого буфера истории скоростей (3 минуты при тике 4 с)
_HIST_DEFAULT_MAXLEN = 45


class UserHist:
    """История скоростей пользователя (LRU-кольцевой буфер).

    up/dn — deque последних N замеров скоростей (B/s).
    p_up/p_dn — пиковые значения за всё время наблюдения.
    n — общее количество добавленных замеров.
    """
    __slots__ = ('up', 'dn', 'p_up', 'p_dn', 'n')

    def __init__(self, maxlen: int = _HIST_DEFAULT_MAXLEN) -> None:
        self.up: deque[float]  = deque(maxlen=maxlen)
        self.dn: deque[float]  = deque(maxlen=maxlen)
        self.p_up: float = 0.0
        self.p_dn: float = 0.0
        self.n: int      = 0

    def add(self, su: float, sd: float) -> None:
        self.up.append(su)
        self.dn.append(sd)
        self.p_up = max(self.p_up, su)
        self.p_dn = max(self.p_dn, sd)
        self.n += 1

    def reset_peaks(self) -> None:
        """Сброс пиковых значений (напр. при переподключении)."""
        self.p_up = max(self.up) if self.up else 0.0
        self.p_dn = max(self.dn) if self.dn else 0.0


ConnEventKind = Literal["connect", "disconnect"]


class ConnEvent:
    """Событие подключения / отключения пользователя.

    Атрибуты:
        kind: "connect" или "disconnect"
        email: идентификатор пользователя (email@tag)
        ip: IP-адрес клиента (может быть пустым для user-level событий)
        geo: геолокация (заполняется позже при рендере)
        ts: unix timestamp создания события
    """
    __slots__ = ('kind', 'email', 'ip', 'geo', 'ts')

    def __init__(self, kind: ConnEventKind, email: str,
                 ip: str = "", geo: str = "") -> None:
        self.kind: ConnEventKind = kind
        self.email: str = email
        self.ip: str    = ip
        self.geo: str   = geo
        self.ts: float  = time.time()
