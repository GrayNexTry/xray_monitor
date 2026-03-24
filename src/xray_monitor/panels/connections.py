"""Панель журнала подключений."""

from __future__ import annotations

import time
from datetime import datetime
from typing import TYPE_CHECKING

from rich.text import Text

from ..constants import C, L
from ..utils import fmt_ts, H

if TYPE_CHECKING:
    from ..app import XrayMonitor


def _fmt_ago(ts: float) -> str:
    """Возвращает 'X мин назад' / 'X ч назад' / дату."""
    diff = time.time() - ts
    if diff < 60:
        return "только что"
    if diff < 3600:
        return f"{int(diff / 60)} мин назад"
    if diff < 86400:
        return f"{int(diff / 3600)} ч назад"
    return datetime.fromtimestamp(ts).strftime("%d.%m %H:%M")


def render_connections(app: "XrayMonitor") -> Text:
    t = Text()
    t.append(f" {L['conn_log']}\n", C["accent"])
    t.append("  " + H * 78 + "\n", C["dim"])

    # Текущие активные IP из gRPC (точный источник "онлайн")
    grpc_online_ips: set = set()
    for ips in app.xray._prev_ips.values():
        grpc_online_ips.update(ips)

    # ── Сводная таблица IP ───────────────────────────────────
    # Собираем все известные IP из log_tail (за 24 ч) + текущие gRPC
    all_ips: dict = {}  # ip -> {email, ts, online}

    for email, ips in app.log_tail.client_ips.items():
        for ip, ts in ips.items():
            if ip not in all_ips or ts > all_ips[ip]["ts"]:
                all_ips[ip] = {"email": email, "ts": ts,
                                "online": ip in grpc_online_ips}

    # Добавляем IP из gRPC если их нет в логе
    for email, ips in app.xray._prev_ips.items():
        for ip in ips:
            if ip not in all_ips:
                all_ips[ip] = {"email": email, "ts": time.time(), "online": True}

    if all_ips:
        # Сортируем: сначала онлайн, потом по времени
        sorted_ips = sorted(all_ips.items(),
                            key=lambda x: (not x[1]["online"], -x[1]["ts"]))

        t.append(f"  {'IP':<20} {'Последний вход':<18} {'Статус':<12} Локация\n", C["dim"])
        t.append("  " + H * 72 + "\n", C["dim"])

        for ip, info in sorted_ips:
            is_on  = info["online"]
            ts     = info["ts"]
            dot    = "●" if is_on else "○"
            dot_c  = C["online"] if is_on else C["offline"]
            ago    = _fmt_ago(ts)
            time_s = fmt_ts(ts)

            t.append(f"  {dot} ", dot_c)
            t.append(f"{ip:<19}", "bold" if is_on else C["dim"])
            t.append(f"{time_s}  {ago:<16}", C["dim"])

            if is_on:
                t.append("онлайн      ", C["online"])
            else:
                t.append(f"{'':12}", "")

            if app.geo_on:
                geo_str = app.geo.fmt(ip)
                if geo_str and geo_str != "...":
                    t.append(geo_str, C["accent2"])
            t.append("\n")

        t.append("  " + H * 72 + "\n\n", C["dim"])
    else:
        t.append(f"\n  {L['no_conn_log']}\n\n", C["dim"])

    # ── Журнал событий ───────────────────────────────────────
    evs = list(app.xray.conn_events)
    if evs:
        today_cnt = sum(1 for e in evs if e.kind == "connect" and time.time() - e.ts < 86400)
        t.append(f"  СОБЫТИЯ  total: {len(evs)}   сегодня: {today_cnt}\n", C["dim"])
        t.append("  " + H * 72 + "\n", C["dim"])

        for ev in reversed(evs[-60:]):
            col  = C["online"] if ev.kind == "connect" else C["offline"]
            icon = "->" if ev.kind == "connect" else "<-"
            name = (ev.email[:26] + "...") if len(ev.email) > 26 else ev.email
            t.append(f"  {fmt_ts(ev.ts)}  ", C["dim"])
            t.append(f"{icon} ", col)
            t.append(f"{name:<28}", "bold" if ev.kind == "connect" else C["dim"])
            if ev.ip:
                t.append(f"  {ev.ip:<18}", C["dim"])
                if app.geo_on:
                    geo_str = app.geo.fmt(ev.ip)
                    if geo_str and geo_str != "...":
                        t.append(geo_str, C["accent2"])
            t.append("\n")

    return t
