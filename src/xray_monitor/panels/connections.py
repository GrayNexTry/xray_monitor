"""Панель журнала подключений."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from rich.text import Text

from ..constants import C, L
from ..utils import fmt_ts, H

if TYPE_CHECKING:
    from ..App import XrayMonitor


def render_connections(app: "XrayMonitor") -> Text:
    t = Text()
    t.append(f" {L['conn_log']}\n", C["accent"])
    t.append("  " + H*78 + "\n", C["dim"])

    evs = list(app.xray.conn_events)
    if not evs:
        t.append(f"\n  {L['no_conn_log']}\n", C["dim"])
        return t

    today = [e for e in evs if e.kind == "connect" and time.time()-e.ts < 86400]
    t.append(f"  Total: {len(evs)}   Today: {len(today)}\n\n", C["dim"])

    for ev in reversed(evs[-80:]):
        col  = C["online"] if ev.kind == "connect" else C["offline"]
        icon = "->" if ev.kind == "connect" else "<-"
        name = (ev.email[:28]+"...") if len(ev.email) > 28 else ev.email
        t.append(f"  {fmt_ts(ev.ts)}  ", C["dim"])
        t.append(f"{icon} ", col)
        t.append(f"{name:<30}", "bold" if ev.kind == "connect" else C["dim"])
        if ev.ip:
            t.append(f"  {ev.ip:<18}", C["dim"])
            if ev.geo: t.append(f" {ev.geo}", C["accent2"])
        t.append("\n")
    return t
