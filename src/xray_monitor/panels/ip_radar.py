"""IP Радар: детальная информация по выбранному IP-адресу."""

from __future__ import annotations

import time
from datetime import datetime
from typing import TYPE_CHECKING

from rich.text import Text

from ..constants import C
from ..utils import fmt_b, H

if TYPE_CHECKING:
    from ..app import XrayMonitor


def _ago(diff: float) -> str:
    if diff < 60:       return "только что"
    if diff < 3600:     return f"{int(diff / 60)} мин назад"
    if diff < 86400:    return f"{int(diff / 3600)} ч назад"
    return f"{int(diff / 86400)} дн назад"


def render_ip_detail(app: "XrayMonitor", ip: str) -> Text:
    """Детальная панель для одного IP-адреса."""
    from ..modules.sni_radar import classify

    t = Text()
    now = time.time()

    t.append(f"  IP: {ip}\n", C["accent"])
    t.append("  " + H * 60 + "\n", C["dim"])

    # ── Пользователь ─────────────────────────────────────────
    rec = app.ip_registry.get_record(ip)
    email = rec.email if rec else ""

    if email:
        t.append(f"  Пользователь:  ", C["accent2"])
        t.append(f"{email}\n", "bold")

    # ── Временны́е метки ──────────────────────────────────────
    first_ts    = rec.first_seen  if rec else 0
    last_active = rec.last_active if rec else 0

    if first_ts > 0:
        t.append(f"  Первый раз:    ", C["accent2"])
        t.append(f"{datetime.fromtimestamp(first_ts).strftime('%d.%m.%Y  %H:%M')}\n",
                 C["dim"])

    if last_active > 0:
        diff = now - last_active
        t.append(f"  Последний раз: ", C["accent2"])
        t.append(
            f"{datetime.fromtimestamp(last_active).strftime('%d.%m.%Y  %H:%M')}"
            f"   ({_ago(diff)})\n",
            C["dim"],
        )

    t.append("\n")

    # ── Геолокация ────────────────────────────────────────────
    if app.geo_on:
        geo = app.geo.lookup(ip)
        if geo:
            cc      = geo.get("cc", "")
            country = geo.get("country", "")
            city    = geo.get("city", "")
            isp     = geo.get("isp", "") or geo.get("asname", "")
            asn     = geo.get("asn", "")

            if country or cc:
                t.append(f"  Страна:    ", C["accent2"])
                t.append(f"{country}  [{cc}]\n" if country else f"[{cc}]\n", "bold")
            if city:
                t.append(f"  Город:     ", C["accent2"])
                t.append(f"{city}\n", C["dim"])
            if isp:
                t.append(f"  Провайдер: ", C["accent2"])
                t.append(f"{isp}\n", C["dim"])
            if asn:
                t.append(f"  ASN:       ", C["accent2"])
                t.append(f"{asn}\n", C["dim"])
            t.append("\n")

    # ── Трафик ───────────────────────────────────────────────
    ip_up, ip_dn = app.ip_registry.get_ip_bytes(ip)
    up = int(ip_up)
    dn = int(ip_dn)

    if up > 0 or dn > 0:
        t.append("  Накопленный трафик:\n", C["accent"])
        t.append(f"    Загружено (DN):  ", C["accent2"])
        t.append(f"{fmt_b(dn)}\n", C["dn"])
        t.append(f"    Отдано    (UP):  ", C["accent2"])
        t.append(f"{fmt_b(up)}\n", C["up"])
        t.append(f"    Итого:           ", C["accent2"])
        t.append(f"{fmt_b(up + dn)}\n", C["total"])
        t.append("\n")

    # ── SNI: объединяем из БД и памяти ───────────────────────
    db_sni = app.traffic_log.query_ip_sni(ip)  # [(domain, tag, hits, last_seen), ...]
    # Данные из БД
    sni_map: dict = {}   # domain -> {"hits", "tag", "last_seen"}
    for domain, tag, hits, last_seen in db_sni:
        sni_map[domain] = {"hits": hits, "tag": tag, "last_seen": last_seen}

    # Добавляем из памяти (только те, которых нет в БД — чтобы не дублировать)
    mem_buf = app.ip_registry.get_ip_sni(ip)
    if mem_buf:
        mem_counts: dict = {}
        for domain, _ts in mem_buf:
            mem_counts[domain] = mem_counts.get(domain, 0) + 1
        for domain, cnt in mem_counts.items():
            if domain not in sni_map:
                sni_map[domain] = {"hits": cnt, "tag": "", "last_seen": 0}

    if sni_map:
        t.append("  " + H * 60 + "\n", C["dim"])
        t.append("  Посещённые сервисы  (по числу запросов):\n\n", C["accent"])

        sorted_sni = sorted(sni_map.items(), key=lambda x: x[1]["hits"], reverse=True)
        for domain, info in sorted_sni[:25]:
            cls = classify(domain)
            if cls:
                _tag, label, col = cls
                svc_col = C.get(col, C["dim"])
                t.append(f"  {label:<14}", svc_col)
            else:
                t.append(f"  {'':14}", "")

            t.append(f"  {domain:<40}", C["dim"])
            t.append(f"  {info['hits']:>5}x", C["accent3"])

            ls = info.get("last_seen", 0)
            if ls:
                t.append(
                    f"   {datetime.fromtimestamp(ls).strftime('%d.%m %H:%M')}",
                    C["dim"],
                )
            t.append("\n")
    else:
        t.append("  " + H * 60 + "\n", C["dim"])
        t.append("  SNI не обнаружен\n", C["dim"])
        t.append("  (включите sniffing в конфиге Xray)\n", C["dim"])

    return t


def build_ip_table_rows(app: "XrayMonitor") -> list:
    """Строит список строк для DataTable IP-радара.

    Возвращает список dict {key, cells}.
    cells: [dot_text, email_str, ip_str, last_str, dn_str, up_str, svc_str, country_str]
    """
    from ..modules.sni_radar import classify

    now = time.time()

    # ── 1. Определяем онлайн-IP ──────────────────────────────
    grpc_online = app.ip_registry.get_online_ips()

    # ── 2. Все записи из IPRegistry ──────────────────────────
    all_records = app.ip_registry.get_all_records()
    merged: dict = {}   # ip -> dict
    for ip_m, rec in all_records.items():
        merged[ip_m] = {
            "ip":          ip_m,
            "email":       rec.email,
            "up":          int(rec.up),
            "dn":          int(rec.dn),
            "first_seen":  int(rec.first_seen),
            "last_active": int(rec.last_active),
        }

    # ── 3. Сортировка ────────────────────────────────────────
    sort_col = getattr(app, "_ip_sort_col", "last_active")

    def _sort_key(item: tuple) -> tuple:
        ip_k, r = item
        online_first = 0 if ip_k in grpc_online else 1
        la = r.get("last_active", 0)
        if sort_col == "email":
            return (r.get("email", ""), -la)
        if sort_col == "dn":
            return (-r.get("dn", 0), online_first, -la)
        if sort_col == "up":
            return (-r.get("up", 0), online_first, -la)
        if sort_col == "status":
            return (online_first, -la)
        # default: last_active
        return (-la, online_first)

    sorted_items = sorted(merged.items(), key=_sort_key)

    # ── 4. Строим строки ─────────────────────────────────────
    rows = []
    for ip_k, info in sorted_items:
        is_on   = ip_k in grpc_online
        last_ts = info.get("last_active", 0)

        dot = Text("●" if is_on else "○",
                   style=C["online"] if is_on else C["offline"])

        em_s = info.get("email", "?")
        em_s = (em_s[:19] + "…") if len(em_s) > 20 else em_s

        # Последний раз
        if is_on:
            last_s = "онлайн"
        elif last_ts > 0:
            diff = now - last_ts
            if diff < 60:
                last_s = "только что"
            elif diff < 3600:
                last_s = f"{int(diff/60)} мин"
            elif diff < 86400:
                last_s = f"{int(diff/3600)} ч назад"
            else:
                last_s = datetime.fromtimestamp(last_ts).strftime("%d.%m %H:%M")
        else:
            last_s = "—"

        dn_v = info.get("dn", 0)
        up_v = info.get("up", 0)
        dn_s = fmt_b(dn_v) if dn_v > 0 else "—"
        up_s = fmt_b(up_v) if up_v > 0 else "—"

        # Топ-сервис (из памяти SNI)
        svc_s = ""
        mem_buf = app.ip_registry.get_ip_sni(ip_k)
        if mem_buf:
            dc: dict = {}
            for dom, _ in mem_buf:
                dc[dom] = dc.get(dom, 0) + 1
            if dc:
                top_dom = max(dc, key=dc.get)    # type: ignore[arg-type]
                cls = classify(top_dom)
                svc_s = cls[1] if cls else top_dom[:13]

        # Страна
        country_s = ""
        if app.geo_on:
            gs = app.geo.fmt(ip_k)
            if gs and gs != "...":
                country_s = gs[:18]

        rows.append({
            "key":   ip_k,
            "cells": [dot, em_s, ip_k, last_s, dn_s, up_s, svc_s, country_s],
        })

    return rows
